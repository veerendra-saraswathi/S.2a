# Compression benchmark review

- Resolution requirement: an indicative input resolution of approximately 1920 × 1200 pixels
- Recorded encoder dimensions (original collections): 1920x1080 (1), 640x512 (6)
- Resolution acceptance: unassessed
- Original deadline misses: 32 / 1000 frames
- flirv_ePoikf repeat_1: 0 / 150 deadline misses/frames
- flirv_ePoikf repeat_2: 0 / 150 deadline misses/frames
- Source reports matching snapshot: 8 / 8

## Run evidence

| Collection | Series | Misses | Frames | Source report |
| --- | --- | ---: | ---: | --- |
| crowd-run-1080p-luma | baseline | 0 | 100 | `benchmarks/crowd-run-1080p-luma/run.json` |
| flirv_4FRnNp | baseline | 0 | 150 | `benchmarks/flir-4frnnp/run.json` |
| flirv_5RSrbW | baseline | 0 | 150 | `benchmarks/flir-six-clips/5RSrbW.json` |
| flirv_6tLtjd | baseline | 4 | 150 | `benchmarks/flir-six-clips/6tLtjd.json` |
| flirv_SCiKdG | baseline | 1 | 150 | `benchmarks/flir-six-clips/SCiKdG.json` |
| flirv_ePoikf | baseline | 17 | 150 | `benchmarks/flir-six-clips/ePoikf.json` |
| flirv_ePoikf | repeat_1 | 0 | 150 | `benchmarks/flir-epoikf-repeat/frame-times.csv` |
| flirv_ePoikf | repeat_2 | 0 | 150 | `benchmarks/flir-epoikf-repeat/frame-times.csv` |
| flirv_vbrSzr | baseline | 10 | 150 | `benchmarks/flir-six-clips/vbrSzr.json` |

## Source verification

| Source report | SHA-256 at import | Local verification |
| --- | --- | --- |
| `benchmarks/crowd-run-1080p-luma/run.json` | `0ca789156a9fb6f05951405b0a44aac80b930873e1ef6a60a23bacb46b0cf621` | Matched |
| `benchmarks/flir-4frnnp/run.json` | `487dede6809d5b5c16fded743c91af28f9ee2177475569123d69a0986b168ef0` | Matched |
| `benchmarks/flir-epoikf-repeat/frame-times.csv` | `e85813fd83b4bce856a152df583488f3cf4dee19f7e3d1cf162b0fed754777c6` | Matched |
| `benchmarks/flir-six-clips/5RSrbW.json` | `20a5e7c48efe371681f8b1a0570e261881e726f18ce2ae71af7555a2913313c0` | Matched |
| `benchmarks/flir-six-clips/6tLtjd.json` | `dbcc14cf8826aed8e52e940b7164173f6208638eef0bf5dabea2cef54c55bad2` | Matched |
| `benchmarks/flir-six-clips/SCiKdG.json` | `3d020e42bcba632571cd626f656e283a92c894458cb35f1192fdefb529b3c6dc` | Matched |
| `benchmarks/flir-six-clips/ePoikf.json` | `c8664769fdc01626784176be8cb49d07350f59f48327055396122dbe1b326db0` | Matched |
| `benchmarks/flir-six-clips/vbrSzr.json` | `95998d179457ec6aec60d15875ac1c2686ed5a5726f4cd2a2ebb40014c16eedb` | Matched |
