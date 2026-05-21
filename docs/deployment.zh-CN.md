# 部署说明

English: [deployment.en.md](deployment.en.md)

## 项目来源

本项目把 `omnivoice-server` 的 API/服务层、`omnivoice-triton` 的
Triton/hybrid 推理后端，以及上游 `k2-fsa/OmniVoice` 中位于
`omnivoice-triton-server/modeling` 的模型运行时代码合并成一个可部署服务。

合并到同一个代码树是为了让 API worker、socket IPC、batch 调度、CUDA Graph
形状规划、Triton kernel 和 OmniVoice 模型调用可以统一调优。

## 运行结构

- FastAPI worker 负责请求校验、文本 chunking、duration 拆分、clone 参考音频读取、响应格式化和 SSE framing。
- GPU inferer 是独立子进程。每个 inferer 占用一个 visible GPU，并在该 GPU 上保留一份模型权重。
- worker 和 inferer 之间通过本机 asyncio TCP socket 通讯。
- `/metrics` 读取 inferer 写入的 shared-memory 快照，不需要阻塞正在进行的推理。
- clone prompt cache 的元数据跨进程共享，GPU inferer 保留本地 prompt tensor 以便复用。

CPU inferer 已移除。扩容方式是增加 GPU inferer 进程。

## 启动

```bash
pip install omnivoice-triton-server
# 可选 attention kernel：
pip install "omnivoice-triton-server[flash]"
pip install "omnivoice-triton-server[sage]"

CUDA_VISIBLE_DEVICES=0,1 \
omnivoice-triton-server start \
  --port 9194 \
  --model-id /path/to/OmniVoice \
  --gpu-inferer 2 \
  --max-batch-size 16 \
  --max-batch-latency 250 \
  --cuda-stream-count 2 \
  --runner-mode hybrid \
  --attn-backend sdpa \
  --default-num-step 32
```

`scripts/start_server.sh` 只是源码树里的 POSIX shell 包装器，底层仍然调用
`python -m omnivoice-triton-server start`。服务默认值在 `omnivoice-triton-server/config.py`，可用
CLI 参数或 `OMNIVOICE_*` 环境变量覆盖。

如果没有指定 `--fastapi-workers`，launcher 会使用 effective GPU inferer 数量作为
worker 数。`--gpu-inferer` 大于 visible GPU 数量时会被 clamp 到 visible GPU 数量。
如果最终没有可启动的 GPU inferer，启动会失败。

停止命令：

```bash
omnivoice-triton-server stop --port 9194
omnivoice-triton-server stop --pid-file logs/<run-id>/server.pid --no-port
omnivoice-triton-server stop --systemd --service-name omnivoice-server
```

## systemd 服务

pip 安装后可以用 `omnivoice-triton-server install-service` 生成并注册 Linux
systemd unit。命令必须传入 CUDA device 列表，`--` 后面的参数会原样传给
`omnivoice-triton-server start`。

```bash
omnivoice-triton-server install-service \
  --cuda-visible-devices 0,1 \
  --python "$(command -v python)" \
  --service-name omnivoice-server \
  --working-dir "$PWD" \
  -- \
  --port 9194 \
  --model-id /path/to/OmniVoice \
  --gpu-inferer 2 \
  --max-batch-size 16 \
  --max-batch-latency 250 \
  --cuda-stream-count 2 \
  --runner-mode hybrid \
  --default-num-step 32
```

源码树也提供 `scripts/install_systemd_service.sh`，生成相同布局的服务文件：

```bash
scripts/install_systemd_service.sh \
  --cuda-visible-devices 0,1 \
  --python /path/to/python \
  --service-name omnivoice-server \
  -- \
  --port 9194 \
  --model-id /path/to/OmniVoice \
  --gpu-inferer 2 \
  --max-batch-size 16 \
  --max-batch-latency 250 \
  --cuda-stream-count 2 \
  --runner-mode hybrid \
  --default-num-step 32
```

installer 会写入：

- `/etc/omnivoice/<service>.sh`：包含 `CUDA_VISIBLE_DEVICES`、`PYTHONPATH`、额外 `--env KEY=VALUE` 和启动参数的 wrapper。
- `/etc/systemd/system/<service>.service`：调用 wrapper 的 systemd unit。

默认会执行 `systemctl daemon-reload`、设置开机自启并立即重启服务。只想生成文件时使用
`--no-enable` 或 `--no-start`。

## 重要参数

- `--host`, `--port`
- `--fastapi-workers`
- `--model-id`, `--runner-mode`, `--dtype`, `--device`
- `--attn-backend`：`auto`、`sdpa`、`eager`、`flex_attention`、
  `flash_attention_2`、`flash_attention_3`、`flash_attention_4`、`sageattention`
- `--gpu-inferer`
- `--request-timeout-s`, `--infer-start-timeout-s`
- `--max-batch-size`, `--max-batch-latency`
- `--cuda-stream-count`
- `--cuda-graph-min-width`, `--cuda-graph-max-width`
- `--cuda-graph-auto-width-tokens-per-word`, `--cuda-graph-auto-max-width`
- `--default-num-step`, `--guidance-scale`, `--denoise/--no-denoise`, `--t-shift`
- `--position-temperature`, `--class-temperature`, `--layer-penalty-factor`
- `--audio-chunk-duration`, `--audio-chunk-threshold`
- `--postprocess-output/--no-postprocess-output`
- `--max-clone-audio-prompt-cache`
- `--max-continuity-audio-tokens`, `--max-continuity-text-words`
- `--text-chunk-words` 和其他 `--text-chunk-*`
- `--log-dir`, `--log-run-id`, `--log-file`, `--pid-file`, `--log-retention-days`

所有设置也可以用 `OMNIVOICE_*` 环境变量配置。

## API

详细请求参数、语言列表、特殊文本标记和读音控制见：

- 中文：[request.zh-CN.md](request.zh-CN.md)
- English: [request.en.md](request.en.md)

## Chunking

文本切分在 API worker 中完成，不在 inferer 中完成。计数策略：

- CJK、日文假名、韩文、泰文等字符级语言按字符计。
- 非 CJK 文本按空白和标点切 token。
- 数字组和 emoji 可按一个词计。

splitter 会优先保留段落、换行、句号/问号/感叹号、分号/冒号、逗号、空白等语义边界，
然后用评分模型把 chunk 尽量打包到接近目标词数。为保留更好的语义边界或避免太短碎片，
允许受控的 soft overflow。

`chunk_mode`：

- `concurrent`：默认。clone 的所有 chunk 共享同一个 clone prompt；auto/design 先生成第一个 chunk，再把它作为 continuity prompt 给后续 chunk 并发执行。
- `sequential`：每个 chunk 使用前一个 chunk 的输出作为 continuity prompt。
- `none`：仍会 chunk，但根据模型上下文估算更大的 max word count，尽量减少切分；执行逻辑接近 sequential。

## Batching 和 CUDA Graph

scheduler 会跨请求合并 chunk job。当前 grouping key 以 mode 为主，因此 speed、duration、
language 或 prompt 不同的兼容请求仍有机会进入同一 batch。

CUDA Graph 在启动时按 compact shape plan 捕获：

- batch bucket 使用高价值的 2 的幂风格 bucket，最高不超过 effective max batch。
- mandatory capture 会先尝试请求的 max width / max batch；显存余量不足时会自动降低 effective width / batch。
- width bucket 会优先覆盖短中等长度，只有显存余量允许时才加入更贵的宽 shape。
- 运行时输入 pad 到最近的已预热 shape。
- 超出 plan 的宽单 chunk batch 可拆成 graph 可覆盖的 microbatch，避免直接大 eager fallback。

`/metrics` 会暴露 graph entries、hits、misses、capture failures、skipped shapes、
requested/effective width 与 batch、显存快照、batch counters、queue age 和错误计数。

## Metrics

```bash
curl http://127.0.0.1:9194/health
curl http://127.0.0.1:9194/metrics
```

常用字段：

- `pending_tasks`
- `queued_batches`, `queued_tasks`
- `running_batches`
- `total_batches`, `total_tasks`, `total_errors`
- `avg_batch_size`, `avg_batch_elapsed_s`, `avg_queue_wait_ms`
- `max_batch_size_seen`, `last_batch`
- `total_pcm_bytes`, `total_empty_audio_fallbacks`
- `cuda_graph_cache`

## Benchmark

这些数字只用于说明该硬件级别和启动配置下的容量，不是跨硬件承诺。

- 测试硬件：2 x NVIDIA GeForce RTX 3080, 20 GiB each。
- 启动参数：`--gpu-inferer 2 --fastapi-workers 2 --runner-mode hybrid --dtype fp16 --max-batch-size 16 --max-batch-latency 250 --cuda-stream-count 2 --default-num-step 32`。
- 负载：短文本请求，目标到达率 100 req/s。

### 吞吐

| 负载 | 总耗时 | 完成 req/s | 生成音频时长 | 音频实时倍数 | RTF |
| --- | ---: | ---: | ---: | ---: | ---: |
| 短文本 speech/design，`num_step=16`，1000 请求 | 36.717 s | 27.235 | 785.980 s | 21.408x | 0.0467 |
| 短文本 speech/design，`num_step=32`，1000 请求 | 62.998 s | 15.874 | 785.690 s | 12.472x | 0.0802 |

`音频实时倍数 = 生成音频时长 / 总耗时`，`RTF = 总耗时 / 生成音频时长`。

### 调度效率

| 负载 | 客户端请求 | 后端任务 | 后端 batch | 任务/backend batch |
| --- | ---: | ---: | ---: | ---: |
| 短文本 speech/design，`num_step=16` | 1,000 | 1,000 | 83 | 12.048 |
| 短文本 speech/design，`num_step=32` | 1,000 | 1,000 | 67 | 14.925 |

### CUDA Graph

- 每个 inferer 预热 15 个 graph。
- 每个 inferer 的形状：
  `(2,8,128)`, `(2,8,160)`, `(2,8,256)`, `(2,8,512)`,
  `(2,8,640)`, `(8,8,64)`, `(8,8,128)`, `(8,8,160)`, `(8,8,256)`,
  `(16,8,128)`, `(16,8,160)`, `(16,8,256)`,
  `(32,8,64)`, `(32,8,128)`, `(32,8,160)`。
- 本次启动中可选 `(4,8,512)` 被 memory headroom guard 跳过，避免强行 capture 导致 OOM。

## 测试命令

```bash
PYTHONPATH=omnivoice-triton-server python tests/test_chunking.py
python tests/test_api.py
python tests/load_1000_rps100.py \
  --total 1000 \
  --rate 100 \
  --concurrency-limit 512 \
  --out tmp/test-artifacts/load_1000_rps100_results.json

python tests/load_mixed_1000.py \
  --total 1000 \
  --rate 100 \
  --concurrency 512 \
  --chunk-mode concurrent \
  --ref-audio /path/to/ref.wav \
  --out tmp/test-artifacts/mixed_1000_results.json
```

快速语法检查：

```bash
python -m py_compile omnivoice-triton-server/*.py
```

生成产物写入已 ignore 的 `tmp/`、`logs/` 和 `run/`。模型权重和导出的媒体文件也已 ignore。

## 当前限制

- 目前只实现 `wav` 和 raw `pcm` 输出。
- SSE 是 chunk-level 兼容流，不是模型内部 streaming。
- socket protocol 使用 newline-delimited JSON，音频 payload 为 base64。
- 未知 `extra_fields` 会保留，但暂未转发进 `model.generate`。
