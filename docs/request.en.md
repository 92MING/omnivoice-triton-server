# Request API

中文: [request.zh-CN.md](request.zh-CN.md)


## Overview

The server exposes three TTS endpoints:

| Endpoint | Content-Type | Purpose |
| --- | --- | --- |
| `POST /v1/audio/speech` | `application/json` | OpenAI-compatible speech API. Select auto/design through `voice` or `instructions`. |
| `POST /v1/audio/design` | `multipart/form-data` | Explicit voice design. Requires `instruct`. |
| `POST /v1/audio/clone` | `multipart/form-data` | Voice cloning. Requires reference audio and `ref_text`. |

Audio is returned at `24000 Hz`; responses include `X-Audio-Sample-Rate: 24000`. Only `wav` and raw `pcm` are currently implemented.

`language` accepts either a language name or a language ID. Examples: `English` / `en`, `Chinese` / `zh`, `Cantonese` / `yue`. Unknown languages are not fatal; the model falls back to language-agnostic mode.

When `language` is Chinese or Cantonese related, for example `zh`, `Chinese`,
`yue`, or `Cantonese`, the server normalizes Traditional Chinese to Simplified
Chinese before chunking with OpenCC `t2s`. It intentionally does not use `hk2s`,
`tw2s`, or other modes that can rewrite words.

## `POST /v1/audio/speech`

JSON body parameters:

| Parameter | Type | Default | Range / values | Description |
| --- | --- | --- | --- | --- |
| `model` | string | `tts-1` | `omnivoice`, `tts-1`, `tts-1-hd` | OpenAI-compatible model field. The current service routes these to the same OmniVoice model. |
| `input` | string | required | non-empty | Text to synthesize. |
| `voice` | string | `auto` | `auto`, OpenAI voice preset, `design:<instruction>`, or any design instruction | `auto` uses auto voice. Presets and arbitrary non-auto strings are converted into design instructions. |
| `speaker` | string | `null` | same as `voice` | Alias for `voice`; if provided, `speaker` takes precedence. |
| `instructions` | string | `null` | any design instruction | Explicit design instruction; forces design mode and overrides `voice`. |
| `response_format` | string | `wav` | `wav`, `pcm` | Output format. `pcm` is raw signed 16-bit little-endian mono PCM. |
| `speed` | number | `1.0` | `0.25` to `4.0` | Speaking speed factor. `duration` takes precedence when both are provided. |
| `duration` | number | `null` | `0.05` to `120.0` seconds | Target output duration. For long text, duration is split across chunks by estimated word count. |
| `language` | string | `null` | see language list | Language hint. Correct language hints are usually more stable. |
| `chunk_mode` | string | `concurrent` | `concurrent`, `sequential`, `none` | Long-text chunk execution strategy. |
| `num_step` | integer | server `--default-num-step`, default `32` | `1` to `128` | Per-request generation step count. Lower values are usually faster and may reduce quality. |
| `stream` | boolean | `false` | `true`, `false` | If `true`, returns SSE chunks encoded as `pcm16_base64`. |
| `request_timeout_s` | number | server `--request-timeout-s`, default `300` | `1.0` to `1200.0` seconds | Per-request timeout. |
| `extra_fields` | object | `{}` | any JSON object | Unknown JSON fields are collected here and echoed through a response header; they are not currently forwarded into model inference. |

Client requests may not supply server-controlled fields: `audio_chunk_duration`, `audio_chunk_threshold`, `batch_mode`, `position_temperature`, `postprocess_output`.

`chunk_mode` values:

| Value | Description |
| --- | --- |
| `concurrent` | Default. Clone chunks share the same clone prompt. Auto/design generate chunk 0 first, use it as continuity prompt, then run remaining chunks concurrently. |
| `sequential` | Each chunk uses the previous generated chunk as continuity prompt, one by one. |
| `none` | Still chunks text, but estimates a larger chunk size from the model context limit to minimize splitting; execution is close to `sequential`. |

Example:

```bash
curl -X POST http://127.0.0.1:9194/v1/audio/speech \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "tts-1",
    "input": "Hello from OmniVoice.",
    "voice": "auto",
    "response_format": "wav",
    "language": "en",
    "num_step": 32,
    "chunk_mode": "concurrent"
  }' \
  --output speech.wav
```

SSE example:

```bash
curl -N -X POST http://127.0.0.1:9194/v1/audio/speech \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "tts-1",
    "input": "Hello from streaming speech.",
    "voice": "auto",
    "stream": true
  }'
```

SSE events are `speech.accepted`, `speech.audio.delta`, and `speech.audio.done`, followed by `data: [DONE]`. The `audio` field in `speech.audio.delta` is base64 encoded 24 kHz mono PCM16.

## Text Markup And Pronunciation Control

The `input` / `text` value is passed through to the model text tokenizer. Based on the official OmniVoice README and the current server code, the following inline markup is supported.

### Non-verbal sound tags

Write these tags directly in text using square brackets. The current server tokenizes them as standalone segments so they do not merge with surrounding Chinese or English text.

| Tag | Meaning |
| --- | --- |
| `[laughter]` | laughter |
| `[sigh]` | sigh |
| `[confirmation-en]` | English confirmation intonation |
| `[question-en]` | English question intonation |
| `[question-ah]` | question sound `ah` |
| `[question-oh]` | question sound `oh` |
| `[question-ei]` | question sound `ei` |
| `[question-yi]` | question sound `yi` |
| `[surprise-ah]` | surprise sound `ah` |
| `[surprise-oh]` | surprise sound `oh` |
| `[surprise-wa]` | surprise sound `wa` |
| `[surprise-yo]` | surprise sound `yo` |
| `[dissatisfaction-hnn]` | dissatisfied humming sound |

Example:

```json
{
  "input": "[laughter] You really got me. I didn't see that coming at all.",
  "voice": "auto"
}
```

### Chinese pronunciation control

For Chinese polyphones or words that need correction, write uppercase pinyin plus a tone number at the character position. Tone numbers follow normal pinyin tones `1` to `4`; neutral tone behavior should be validated for your text.

Example:

```json
{
  "input": "这批货物打ZHE2出售后他严重SHE2本了，再也经不起ZHE1腾了。",
  "language": "zh"
}
```

Here `ZHE2`, `SHE2`, and `ZHE1` override the default pronunciation near those characters. In practice, mark only the characters that need correction instead of converting the whole sentence to pinyin.

### English pronunciation control

For English, use CMU Pronouncing Dictionary style phonemes: uppercase phonemes inside square brackets.

Example:

```json
{
  "input": "He plays the [B EY1 S] guitar while catching a [B AE1 S] fish.",
  "language": "en"
}
```

Markup can affect chunk word-count estimation and speaking rhythm. For long text, prefer placing tags near semantic boundaries rather than inside very short phrases.

## Voice presets

`voice` supports these OpenAI-style presets. The server maps them to design instructions:

| voice | design instruction |
| --- | --- |
| `alloy` | `middle-aged, moderate pitch` |
| `ash` | `male, young adult, low pitch` |
| `ballad` | `female, young adult, moderate pitch` |
| `coral` | `female, young adult, high pitch` |
| `echo` | `male, middle-aged, moderate pitch` |
| `fable` | `male, young adult, british accent` |
| `nova` | `female, young adult, moderate pitch` |
| `onyx` | `male, middle-aged, very low pitch` |
| `sage` | `female, middle-aged, moderate pitch` |
| `shimmer` | `female, young adult, very high pitch` |

`voice` can also be `design:female, young adult, moderate pitch`, or any arbitrary string treated as a design instruction.

## `POST /v1/audio/design`

`multipart/form-data` parameters:

| Parameter | Type | Default | Range / values | Description |
| --- | --- | --- | --- | --- |
| `text` | string | required | non-empty | Text to synthesize. |
| `instruct` | string | required | voice design instruction | Describes voice, age, gender, pitch, accent, and style. |
| `language` | string | `null` | see language list | Language hint. |
| `language_id` | string | `null` | see language list | Alias for `language`; `language` takes precedence. |
| `speed` | number | `1.0` | recommended `0.25` to `4.0` | Speaking speed factor. |
| `duration` | number | `null` | recommended `0.05` to `120.0` seconds | Target output duration. |
| `chunk_mode` | string | `concurrent` | `concurrent`, `sequential`, `none` | Long-text chunk execution strategy. |
| `num_step` | integer | server `--default-num-step`, default `32` | `1` to `128` | Generation step count. |
| `response_format` | string | `wav` | `wav`, `pcm` | Output format. |
| `request_timeout_s` | number | server default | `1.0` to `1200.0` seconds | Per-request timeout. |

Example:

```bash
curl -X POST http://127.0.0.1:9194/v1/audio/design \
  -F 'text=Hello from a designed voice.' \
  -F 'instruct=female, young adult, moderate pitch' \
  -F 'language=en' \
  -F 'num_step=32' \
  -F 'response_format=wav' \
  --output design.wav
```

## `POST /v1/audio/clone`

`multipart/form-data` parameters:

| Parameter | Type | Default | Range / values | Description |
| --- | --- | --- | --- | --- |
| `text` | string | required | non-empty | Text to synthesize. |
| `ref_audio` | file | required unless `ref_audio_base64` is provided | audio file | Uploaded reference audio. |
| `ref_audio_base64` | string | required unless `ref_audio` is provided | base64 or data URL | Reference audio bytes as base64. |
| `ref_text` | string | required | non-empty | Transcript for the reference audio. This is required; inferer-side ASR is disabled. |
| `language` | string | `null` | see language list | Language hint. |
| `language_id` | string | `null` | see language list | Alias for `language`; `language` takes precedence. |
| `speed` | number | `1.0` | recommended `0.25` to `4.0` | Speaking speed factor. |
| `duration` | number | `null` | recommended `0.05` to `120.0` seconds | Target output duration. |
| `chunk_mode` | string | `concurrent` | `concurrent`, `sequential`, `none` | Long-text chunk execution strategy. |
| `num_step` | integer | server `--default-num-step`, default `32` | `1` to `128` | Generation step count. |
| `response_format` | string | `wav` | `wav`, `pcm` | Output format. |
| `request_timeout_s` | number | server default | `1.0` to `1200.0` seconds | Per-request timeout. |

Example:

```bash
curl -X POST http://127.0.0.1:9194/v1/audio/clone \
  -F 'text=Hello from a cloned voice.' \
  -F 'ref_audio=@ref.wav;type=audio/wav' \
  -F 'ref_text=Text spoken in the reference audio.' \
  -F 'language=en' \
  -F 'num_step=32' \
  -F 'response_format=wav' \
  --output clone.wav
```


## References

- OmniVoice model card: https://huggingface.co/k2-fsa/OmniVoice
- OmniVoice official README, non-verbal and pronunciation control: https://github.com/k2-fsa/OmniVoice#non-verbal--pronunciation-control
- Current server implementation for standalone non-verbal tag tokenization: `src/modeling/models/omnivoice.py`

## Supported Languages

The table below is generated from `src/modeling/utils/lang_map.py`. Pass either the `language` ID or the English language name.

Total languages: 646.

| ID | Language name |
| --- | --- |
| `aae` | Arbëreshë Albanian |
| `aal` | Afade |
| `aao` | Algerian Saharan Arabic |
| `ab` | Abkhazian |
| `abb` | Bankon |
| `abn` | Abua |
| `abr` | Abron |
| `abs` | Ambonese Malay |
| `abv` | Baharna Arabic |
| `acm` | Mesopotamian Arabic |
| `acw` | Hijazi Arabic |
| `acx` | Omani Arabic |
| `adf` | Dhofari Arabic |
| `adx` | Amdo Tibetan |
| `ady` | Adyghe |
| `aeb` | Tunisian Arabic |
| `aec` | Saidi Arabic |
| `af` | Afrikaans |
| `afb` | Gulf Arabic |
| `afo` | Eloyi |
| `ahl` | Igo |
| `ahs` | Ashe |
| `ajg` | Aja (Benin) |
| `aju` | Judeo-Moroccan Arabic |
| `ala` | Alago |
| `aln` | Gheg Albanian |
| `alo` | Larike-Wakasihu |
| `am` | Amharic |
| `amu` | Guerrero Amuzgo |
| `an` | Aragonese |
| `anc` | Ngas |
| `ank` | Goemai |
| `anp` | Angika |
| `anw` | Anaang |
| `aom` | Ömie |
| `apc` | Levantine Arabic |
| `apd` | Sudanese Arabic |
| `arb` | Standard Arabic |
| `arq` | Algerian Arabic |
| `ars` | Najdi Arabic |
| `ary` | Moroccan Arabic |
| `arz` | Egyptian Arabic |
| `as` | Assamese |
| `ast` | Asturian |
| `avl` | Eastern Egyptian Bedawi Arabic |
| `awo` | Awak |
| `ayl` | Libyan Arabic |
| `ayp` | North Mesopotamian Arabic |
| `az` | Azerbaijani |
| `ba` | Bashkir |
| `bag` | Tuki |
| `bas` | Basa (Cameroon) |
| `bax` | Bamun |
| `bba` | Baatonum |
| `bbj` | Ghomálá' |
| `bbl` | Bats |
| `bbu` | Kulung (Nigeria) |
| `bce` | Bamenyam |
| `bci` | Baoulé |
| `bcs` | Kohumono |
| `bcy` | Bacama |
| `bda` | Bayot |
| `bde` | Bade |
| `bdm` | Buduma |
| `be` | Belarusian |
| `beb` | Bebele |
| `bew` | Betawi |
| `bfd` | Bafut |
| `bft` | Balti |
| `bg` | Bulgarian |
| `bgp` | Eastern Balochi |
| `bhb` | Bhili |
| `bhh` | Bukharic |
| `bho` | Bhojpuri |
| `bhp` | Bima |
| `bhr` | Bara Malagasy |
| `bjj` | Kanauji |
| `bjk` | Barok |
| `bjn` | Banjar |
| `bjt` | Balanta-Ganja |
| `bkh` | Bakoko |
| `bkm` | Kom (Cameroon) |
| `bky` | Bokyi |
| `bmm` | Northern Betsimisaraka Malagasy |
| `bmq` | Bomu |
| `bn` | Bengali |
| `bnm` | Batanga |
| `bnn` | Bunun |
| `bns` | Bundeli |
| `bo` | Tibetan |
| `bou` | Bondei |
| `bqg` | Bago-Kusuntu |
| `br` | Breton |
| `bra` | Braj |
| `brh` | Brahui |
| `bri` | Mokpwe |
| `brx` | Bodo |
| `bs` | Bosnian |
| `bsh` | Kati |
| `bsj` | Bangwinji |
| `bsk` | Burushaski |
| `btm` | Batak Mandailing |
| `btv` | Bateri |
| `bug` | Buginese |
| `bum` | Bulu (Cameroon) |
| `buo` | Terei |
| `bux` | Boghom |
| `bwr` | Bura-Pabir |
| `bxf` | Bilur |
| `byc` | Ubaghara |
| `bys` | Burak |
| `byv` | Medumba |
| `byx` | Qaqet |
| `bzc` | Southern Betsimisaraka Malagasy |
| `bzw` | Basa (Nigeria) |
| `ca` | Catalan |
| `ccg` | Samba Daka |
| `ceb` | Cebuano |
| `cen` | Cen |
| `cfa` | Dijim-Bwilim |
| `cgg` | Chiga |
| `chq` | Quiotepec Chinantec |
| `cjk` | Chokwe |
| `ckb` | Central Kurdish |
| `ckl` | Cibak |
| `ckr` | Kairak |
| `cky` | Cakfem-Mushere |
| `cnh` | Hakha Chin |
| `cpy` | South Ucayali Ashéninka |
| `cs` | Czech |
| `cte` | Tepinapa Chinantec |
| `ctl` | Tlacoatzintepec Chinantec |
| `cut` | Teutila Cuicatec |
| `cux` | Tepeuxila Cuicatec |
| `cv` | Chuvash |
| `cy` | Welsh |
| `da` | Danish |
| `dag` | Dagbani |
| `dar` | Dargwa |
| `dav` | Taita |
| `dbd` | Dadiya |
| `dcc` | Deccan |
| `de` | German |
| `deg` | Degema |
| `dgh` | Dghwede |
| `dgo` | Dogri |
| `dje` | Zarma |
| `dmk` | Domaaki |
| `dml` | Dameli |
| `dru` | Rukai |
| `dty` | Dotyali |
| `dua` | Duala |
| `dv` | Dhivehi |
| `dyu` | Dyula |
| `dzg` | Dazaga |
| `ebr` | Ebrié |
| `ebu` | Embu |
| `ego` | Eggon |
| `eiv` | Askopan |
| `eko` | Koti |
| `ekr` | Yace |
| `el` | Greek |
| `elm` | Eleme |
| `en` | English |
| `eo` | Esperanto |
| `es` | Spanish |
| `esu` | Central Yupik |
| `et` | Estonian |
| `eto` | Eton (Cameroon) |
| `ets` | Yekhee |
| `etu` | Ejagham |
| `eu` | Basque |
| `ewo` | Ewondo |
| `ext` | Extremaduran |
| `eyo` | Keiyo |
| `fa` | Persian |
| `fan` | Fang (Equatorial Guinea) |
| `fat` | Fanti |
| `ff` | Fulah |
| `ffm` | Maasina Fulfulde |
| `fi` | Finnish |
| `fia` | Nobiin |
| `fil` | Filipino |
| `fip` | Fipa |
| `fkk` | Kirya-Konzəl |
| `fmp` | Fe'fe' |
| `fr` | French |
| `fub` | Adamawa Fulfulde |
| `fuc` | Pulaar |
| `fue` | Borgu Fulfulde |
| `fuf` | Pular |
| `fuh` | Western Niger Fulfulde |
| `fui` | Bagirmi Fulfulde |
| `fuq` | Central-Eastern Niger Fulfulde |
| `fuv` | Nigerian Fulfulde |
| `fy` | Western Frisian |
| `ga` | Irish |
| `gbm` | Garhwali |
| `gbr` | Gbagyi |
| `gby` | Gbari |
| `gcc` | Mali |
| `gdf` | Guduf-Gava |
| `gej` | Gen |
| `ges` | Geser-Gorom |
| `ggg` | Gurgula |
| `gid` | Gidar |
| `gig` | Goaria |
| `giz` | South Giziga |
| `gjk` | Kachi Koli |
| `gju` | Gujari |
| `gl` | Galician |
| `glw` | Glavda |
| `gn` | Guarani |
| `gol` | Gola |
| `gom` | Goan Konkani |
| `gsl` | Gusilay |
| `gu` | Gujarati |
| `gui` | Eastern Bolivian Guaraní |
| `gur` | Farefare |
| `guz` | Gusii |
| `gv` | Manx |
| `gwc` | Gawri |
| `gwe` | Gweno |
| `gwt` | Gawar-Bati |
| `gya` | Northwest Gbaya |
| `gyz` | Geji |
| `ha` | Hausa |
| `hah` | Hahon |
| `hao` | Hakö |
| `haw` | Hawaiian |
| `haz` | Hazaragi |
| `hbb` | Huba |
| `he` | Hebrew |
| `hem` | Hemba |
| `hi` | Hindi |
| `hia` | Lamang |
| `hkk` | Hunjara-Kaina Ke |
| `hla` | Halia |
| `hno` | Northern Hindko |
| `hoj` | Hadothi |
| `hr` | Croatian |
| `hsb` | Upper Sorbian |
| `ht` | Haitian |
| `hu` | Hungarian |
| `hue` | San Francisco Del Mar Huave |
| `hul` | Hula |
| `hux` | Nüpode Huitoto |
| `hwo` | Hwana |
| `hy` | Armenian |
| `hz` | Herero |
| `ia` | Interlingua (International Auxiliary Language Association) |
| `ibb` | Ibibio |
| `id` | Indonesian |
| `ida` | Idakho-Isukha-Tiriki |
| `idu` | Idoma |
| `ig` | Igbo |
| `ijc` | Izon |
| `ijn` | Kalabari |
| `ik` | Inupiaq |
| `ikw` | Ikwere |
| `is` | Icelandic |
| `ish` | Esan |
| `iso` | Isoko |
| `it` | Italian |
| `its` | Isekiri |
| `itw` | Ito |
| `itz` | Itzá |
| `ja` | Japanese |
| `jal` | Yalahatan |
| `jax` | Jambi Malay |
| `jgo` | Ngomba |
| `jmx` | Western Juxtlahuaca Mixtec |
| `jns` | Jaunsari |
| `jqr` | Jaqaru |
| `juk` | Wapan |
| `juo` | Jiba |
| `jv` | Javanese |
| `ka` | Georgian |
| `kab` | Kabyle |
| `kai` | Karekare |
| `kaj` | Jju |
| `kam` | Kamba |
| `kbd` | Kabardian |
| `kbl` | Kanembu |
| `kbt` | Abadi |
| `kcq` | Kamo |
| `kdh` | Tem |
| `kea` | Kabuverdianu |
| `keu` | Akebu |
| `kfe` | Kota (India) |
| `kfk` | Kinnauri |
| `kfp` | Korwa |
| `khg` | Khams Tibetan |
| `khw` | Khowar |
| `kj` | Kuanyama |
| `kjc` | Coastal Konjo |
| `kjk` | Highland Konjo |
| `kk` | Kazakh |
| `kln` | Kalenjin |
| `kls` | Kalasha |
| `km` | Khmer |
| `kmr` | Northern Kurdish |
| `kmy` | Koma |
| `kn` | Kannada |
| `kna` | Dera (Nigeria) |
| `knn` | Konkani |
| `ko` | Korean |
| `kol` | Kol (Papua New Guinea) |
| `koo` | Konzo |
| `kpo` | Ikposo |
| `kqo` | Eastern Krahn |
| `ks` | Kashmiri |
| `ksd` | Kuanua |
| `ksf` | Bafia |
| `kto` | Kuot |
| `kuh` | Kushi |
| `kvx` | Parkari Koli |
| `kw` | Cornish |
| `kwm` | Kwambi |
| `kxp` | Wadiyara Koli |
| `ky` | Kirghiz |
| `kyx` | Rapoisi |
| `lag` | Rangi |
| `lb` | Luxembourgish |
| `lcm` | Tungag |
| `ldb` | DũYa |
| `lg` | Ganda |
| `lij` | Ligurian |
| `lir` | Liberian English |
| `lkb` | Kabras |
| `lla` | Lala-Roba |
| `ln` | Lingala |
| `lnu` | Longuda |
| `lo` | Lao |
| `loa` | Loloda |
| `lrk` | Loarki |
| `lss` | Lasi |
| `lt` | Lithuanian |
| `ltg` | Latgalian |
| `lto` | Tsotso |
| `lua` | Luba-Lulua |
| `luo` | Luo |
| `lus` | Lushai |
| `lv` | Latvian |
| `lwg` | Wanga |
| `mab` | Yutanduchi Mixtec |
| `maf` | Mafa |
| `mai` | Maithili |
| `mau` | Huautla Mazatec |
| `max` | North Moluccan Malay |
| `mbo` | Mbo (Cameroon) |
| `mcf` | Matsés |
| `mcn` | Masana |
| `mcx` | Mpiemo |
| `mdd` | Mbum |
| `mde` | Maba (Chad) |
| `mdf` | Moksha |
| `mek` | Mekeo |
| `mer` | Meru |
| `meu` | Motu |
| `mfm` | Marghi South |
| `mfn` | Cross River Mbembe |
| `mfo` | Mbe |
| `mfv` | Mandjak |
| `mgg` | Mpumpong |
| `mgi` | Lijili |
| `mhk` | Mungaka |
| `mhr` | Eastern Mari |
| `mi` | Maori |
| `mig` | San Miguel El Grande Mixtec |
| `miu` | Cacaloxtepec Mixtec |
| `mk` | Macedonian |
| `mkf` | Miya |
| `mki` | Dhatki |
| `ml` | Malayalam |
| `mlq` | Western Maninkakan |
| `mn` | Mongolian |
| `mne` | Naba |
| `mni` | Manipuri |
| `mqy` | Manggarai |
| `mr` | Marathi |
| `mrj` | Western Mari |
| `mrr` | Maria (India) |
| `mrt` | Marghi Central |
| `ms` | Malay |
| `mse` | Musey |
| `msh` | Masikoro Malagasy |
| `msw` | Mansoanka |
| `mt` | Maltese |
| `mtr` | Mewari |
| `mtu` | Tututepec Mixtec |
| `mtx` | Tidaá Mixtec |
| `mua` | Mundang |
| `mug` | Musgu |
| `mui` | Musi |
| `mve` | Marwari (Pakistan) |
| `mvy` | Indus Kohistani |
| `mxs` | Huitepec Mixtec |
| `mxu` | Mada (Cameroon) |
| `mxy` | Southeastern Nochixtlán Mixtec |
| `my` | Burmese |
| `myv` | Erzya |
| `mzl` | Mazatlán Mixe |
| `nal` | Nalik |
| `nan` | Min Nan Chinese |
| `nap` | Neapolitan |
| `nb` | Norwegian Bokmål |
| `nbh` | Ngamo |
| `ncf` | Notsi |
| `nco` | Sibe |
| `ncx` | Central Puebla Nahuatl |
| `ndi` | Samba Leko |
| `ng` | Ndonga |
| `ngi` | Ngizim |
| `nhg` | Tetelcingo Nahuatl |
| `nhi` | Zacatlán-Ahuacatlán-Tepetzintla Nahuatl |
| `nhn` | Central Nahuatl |
| `nhq` | Huaxcaleca Nahuatl |
| `nja` | Nzanyi |
| `nl` | Dutch |
| `nla` | Ngombale |
| `nlv` | Orizaba Nahuatl |
| `nmg` | Kwasio |
| `nmz` | Nawdm |
| `nn` | Norwegian Nynorsk |
| `nnh` | Ngiemboon |
| `no` | Norwegian |
| `noe` | Nimadi |
| `npi` | Nepali |
| `nso` | Pedi |
| `ny` | Chichewa |
| `nyu` | Nyungwe |
| `oc` | Occitan |
| `odk` | Od |
| `odu` | Odual |
| `ogo` | Khana |
| `om` | Oromo |
| `orc` | Orma |
| `oru` | Ormuri |
| `ory` | Odia |
| `os` | Iron Ossetic |
| `pa` | Panjabi |
| `pbs` | Central Pame |
| `pbt` | Southern Pashto |
| `pbu` | Northern Pashto |
| `pcm` | Nigerian Pidgin |
| `pex` | Petats |
| `phl` | Phalura |
| `phr` | Pahari-Potwari |
| `pip` | Pero |
| `piy` | Piya-Kwonci |
| `pko` | Pökoot |
| `pl` | Polish |
| `plk` | Kohistani Shina |
| `plt` | Plateau Malagasy |
| `pmq` | Northern Pame |
| `pms` | Piemontese |
| `pmy` | Papuan Malay |
| `pnb` | Western Panjabi |
| `poc` | Poqomam |
| `poe` | San Juan Atzingo Popoloca |
| `pow` | San Felipe Otlaltepec Popoloca |
| `prq` | Ashéninka Perené |
| `ps` | Pushto |
| `pst` | Central Pashto |
| `pt` | Portuguese |
| `pua` | Western Highland Purepecha |
| `pwn` | Paiwan |
| `qug` | Chimborazo Highland Quichua |
| `qum` | Sipacapense |
| `qup` | Southern Pastaza Quechua |
| `qur` | Yanahuanca Pasco Quechua |
| `qus` | Santiago del Estero Quichua |
| `quv` | Sacapulteco |
| `qux` | Yauyos Quechua |
| `quy` | Ayacucho Quechua |
| `qva` | Ambo-Pasco Quechua |
| `qvi` | Imbabura Highland Quichua |
| `qvj` | Loja Highland Quichua |
| `qvl` | Cajatambo North Lima Quechua |
| `qwa` | Corongo Ancash Quechua |
| `qws` | Sihuas Ancash Quechua |
| `qxa` | Chiquián Ancash Quechua |
| `qxp` | Puno Quechua |
| `qxt` | Santa Ana de Tusi Pasco Quechua |
| `qxu` | Arequipa-La Unión Quechua |
| `qxw` | Jauja Wanca Quechua |
| `rag` | Logooli |
| `rm` | Romansh |
| `ro` | Romanian |
| `rob` | Tae' |
| `rof` | Rombo |
| `roo` | Rotokas |
| `rth` | Ratahan |
| `ru` | Russian |
| `rup` | Macedo-Romanian |
| `rw` | Kinyarwanda |
| `sa` | Sanskrit |
| `sah` | Yakut |
| `sat` | Santali |
| `sau` | Saleman |
| `say` | Saya |
| `sbn` | Sindhi Bhil |
| `sc` | Sardinian |
| `scl` | Shina |
| `scn` | Sicilian |
| `sd` | Sindhi |
| `sei` | Seri |
| `shu` | Chadian Arabic |
| `si` | Sinhala |
| `sip` | Sikkimese |
| `siw` | Siwai |
| `sjr` | Siar-Lak |
| `sk` | Slovak |
| `skg` | Sakalava Malagasy |
| `skr` | Saraiki |
| `sl` | Slovenian |
| `sn` | Shona |
| `snc` | Sinaugoro |
| `snk` | Soninke |
| `so` | Somali |
| `sol` | Solos |
| `sps` | Saposa |
| `sq` | Albanian |
| `sr` | Serbian |
| `src` | Logudorese Sardinian |
| `sro` | Campidanese Sardinian |
| `ssi` | Sansi |
| `ste` | Liana-Seti |
| `sua` | Sulka |
| `sv` | Swedish |
| `sva` | Svan |
| `sw` | Swahili |
| `szy` | Sakizaya |
| `ta` | Tamil |
| `tan` | Tangale |
| `tar` | Central Tarahumara |
| `tay` | Atayal |
| `tbf` | Mandara |
| `tcf` | Malinaltepec Me'phaa |
| `tcy` | Tulu |
| `tdn` | Tondano |
| `tdx` | Tandroy-Mahafaly Malagasy |
| `te` | Telugu |
| `tg` | Tajik |
| `tgc` | Tigak |
| `th` | Thai |
| `the` | Chitwania Tharu |
| `thq` | Kochila Tharu |
| `thr` | Rana Tharu |
| `thv` | Tahaggart Tamahaq |
| `ti` | Tigrinya |
| `tig` | Tigre |
| `tio` | Teop |
| `tk` | Turkmen |
| `tkg` | Tesaka Malagasy |
| `tkt` | Kathoriya Tharu |
| `tli` | Tlingit |
| `tlp` | Filomena Mata-Coahuitlán Totonac |
| `tn` | Tswana |
| `tok` | Toki Pona |
| `tpl` | Tlacoapa Me'phaa |
| `tpz` | Tinputz |
| `tqp` | Tomoip |
| `tr` | Turkish |
| `trp` | Kok Borok |
| `trq` | San Martín Itunyoso Triqui |
| `trv` | Sediq |
| `trw` | Torwali |
| `tt` | Tatar |
| `ttj` | Tooro |
| `ttr` | Tera |
| `ttu` | Torau |
| `tui` | Tupuri |
| `tul` | Tula |
| `tuq` | Tedaga |
| `tuv` | Turkana |
| `tuy` | Tugen |
| `tvo` | Tidore |
| `tvu` | Tunen |
| `tw` | Twi |
| `twu` | Termanu |
| `txs` | Tonsea |
| `txy` | Tanosy Malagasy |
| `udl` | Wuzlam |
| `ug` | Uighur |
| `uk` | Ukrainian |
| `uki` | Kui (India) |
| `umb` | Umbundu |
| `ur` | Urdu |
| `ush` | Ushojo |
| `uz` | Uzbek |
| `uzn` | Northern Uzbek |
| `vai` | Vai |
| `var` | Huarijio |
| `ver` | Mom Jango |
| `vi` | Vietnamese |
| `vmc` | Juxtlahuaca Mixtec |
| `vmj` | Ixtayutla Mixtec |
| `vmm` | Mitlatongo Mixtec |
| `vmp` | Soyaltepec Mazatec |
| `vmz` | Mazatlán Mazatec |
| `vot` | Votic |
| `vro` | Võro |
| `wbl` | Wakhi |
| `wci` | Waci Gbe |
| `weo` | Wemale |
| `wes` | Cameroon Pidgin |
| `wja` | Waja |
| `wji` | Warji |
| `wo` | Wolof |
| `wof` | Gambian Wolof |
| `xh` | Xhosa |
| `xhe` | Khetrani |
| `xka` | Kalkoti |
| `xmf` | Mingrelian |
| `xmv` | Antankarana Malagasy |
| `xmw` | Tsimihety Malagasy |
| `xpe` | Liberia Kpelle |
| `xti` | Sinicahua Mixtec |
| `xtu` | Cuyamecalco Mixtec |
| `yaq` | Yaqui |
| `yav` | Yangben |
| `yay` | Agwagwune |
| `ydd` | Eastern Yiddish |
| `ydg` | Yidgha |
| `yer` | Tarok |
| `yes` | Nyankpa |
| `yi` | Yiddish |
| `yo` | Yoruba |
| `yue` | Cantonese |
| `zga` | Kinga |
| `zgh` | Standard Moroccan Tamazight |
| `zh` | Chinese |
| `zoc` | Copainalá Zoque |
| `zoh` | Chimalapa Zoque |
| `zor` | Rayón Zoque |
| `zpv` | Chichicapan Zapotec |
| `zpy` | Mazaltepec Zapotec |
| `ztg` | Xanaguía Zapotec |
| `ztn` | Santa Catarina Albarradas Zapotec |
| `ztp` | Loxicha Zapotec |
| `zts` | Tilquiapan Zapotec |
| `ztu` | Güilá Zapotec |
| `zu` | Zulu |
| `zza` | Zaza |
