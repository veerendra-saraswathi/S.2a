# GOES-19 weather scan timing review

Five host runs measured the same two 2500 × 1500 weather rasters in acquisition order. The scans were acquired five minutes apart. The encoder deadline was 66.667 ms (two frame periods at a nominal 30 fps).

| Run | Scan 1 (ms) | Scan 2 (ms) | Deadline misses | Evidence at the reviewed revision |
| --- | ---: | ---: | ---: | --- |
| Baseline JSON | 91.254 | 74.290 | 2 | [Baseline report](https://github.com/veerendra-saraswathi/S0.7/blob/2431b1bb356d75eecb9f99486b2a5bd3e9c37b44/benchmarks/goes19-c13-two-scans/run.json) |
| Recorded replay | 76.997 | 69.005 | 2 | [replay-103e610](https://github.com/veerendra-saraswathi/S0.7/blob/2431b1bb356d75eecb9f99486b2a5bd3e9c37b44/benchmarks/goes19-c13-two-scans/replays/replay-103e610105cb46939d280b32f3aaf240.json) |
| Recorded replay | 87.408 | 61.638 | 1 | [replay-5441e14](https://github.com/veerendra-saraswathi/S0.7/blob/2431b1bb356d75eecb9f99486b2a5bd3e9c37b44/benchmarks/goes19-c13-two-scans/replays/replay-5441e14b014c478d96d619aeccae1281.json) |
| Terminal transcript 1 | 83.630 | 62.894 | 1 | [CSV row 1](https://github.com/veerendra-saraswathi/S0.7/blob/2431b1bb356d75eecb9f99486b2a5bd3e9c37b44/benchmarks/goes19-c13-two-scans/replays/reported-mac-times.csv) |
| Terminal transcript 2 | 73.258 | 54.314 | 1 | [CSV row 2](https://github.com/veerendra-saraswathi/S0.7/blob/2431b1bb356d75eecb9f99486b2a5bd3e9c37b44/benchmarks/goes19-c13-two-scans/replays/reported-mac-times.csv) |

Scan 1 exceeded the deadline in all five runs. Scan 2 exceeded it in two. The recorded replay files carry baseline and output hashes. The two transcript rows preserve operator-reported times. The measurements leave the cause of the variation undetermined.

These two weather scans extend the evidence for large raster inputs. Camera-video resolution acceptance remains unassessed in the [seven-collection review](Samanvaya-compression-review-v4.md).

To reproduce the timing table from S.2a with the sibling S0.7 checkout at revision `2431b1b`, run

```sh
python3 ../S0.7/benchmarks/goes19-c13-two-scans/compare_timings.py
```
