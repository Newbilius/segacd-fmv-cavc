EN / [JP](#jp)

# Simulation Encoding Pipeline

This document describes the processing flow and measured time of
`tools/sim.py`. Disc packing and analysis rendering are outside this timing.

## Flow

```text
Extract -> Palette -> Quantize -> Forecast -> Decide -> Finalize
```

| Stage | Processing |
|---|---|
| **Extract** | Decode the source into encoder frames, comparison frames, and mono audio. |
| **Palette** | Divide the movie into palette segments, train each segment's CRAM palettes, and automatically detect spatially static shots with a black-bounded fade on one or both sides, without source-time inputs. |
| **Quantize** | Convert every frame into palette assignments and indexed 8x8 patterns; automatically detect source-wide hardware-scroll segments without source-time inputs, select the adopted scroll windows and their per-frame plane states, and quantize each window's world-mosaic guard edges; selected fades freeze one exact reference image and derive their inline CRAM steps. |
| **Forecast** | Calculate one-pass future demand, physical delivery limits, quality reserves, boot-preload use split between the frame-0 inline staging area and the boot-VRAM sidecar backside, and mandatory reference-prefetch room for one-sided fade-outs. |
| **Decide** | Select each frame's exact or reused patterns, keep the preceding display's slots live until the final name-table DMA and reject choices whose temporary old-plus-new pattern union would overflow VRAM, allocate slots, retain resident one-sided fade references and collect their cold preloads into a contiguous edge block, catch up any reclaimed entries, reserve each adopted scroll frame's guard-edge supply and clamp its cold and Prg ceilings to the single-run limit, assign pattern sources, and commit the physical budget. A fade preparation deadline that cannot complete demotes its shot to ordinary frames and restarts the encode without that fade. |
| **Finalize** | Plan the shadow update lists over the exact shared-sector pack route, add each scroll frame's rolling-plane name-table words, verify the complete physical schedule, and write the numeric traces and decision log. |

## Working memory

`--array-storage disk` places frame features and quantized numeric caches in
mapped files under the selected scratch root; `memory` uses ordinary arrays.
Frame-feature loading queues a bounded number of tasks. Edge-strength data is
released after palette training, source-feature views after quantization, audio
intermediates after their output is written, and baseline demand/prefetch
objects after boot planning. Final decisions retain the numeric forecast
metrics needed downstream, without keeping duplicate pattern-key sequences.

These changes preserve encoder settings and numeric results. Disk mode can add
I/O latency and needs disk space; it does not bound Python pattern-history or
decision-log growth. A tmpfs scratch root still consumes physical RAM. Use
`harness/runtime_storage/measure.py` to distinguish process RSS, sampled process
tree RSS, system shared memory and filesystem use. The full-source comparison
uses 16,384 frames at 320x224 and compares outputs from the frozen parent with
the current encoder. Its low-motion input measures stage-cache retention, not
the worst-case pattern history of a complex movie.

## Independent multipart planning

The CLI `tools/multipart.py` resolves the part count from `--parts N`, falling
back to `[multipart].parts` in the profile. It counts the source on the output-frame
clock, divides it into contiguous ranges, and runs the complete encoder and
packer for each range. Each part has its own palette, whole-movie quality
budget and physical delivery schedule. Complete parts can be encoded in any
order and reused without repeating their planning. Only the final ISO assembly
requires every part. No browser workflow is added.

Shorter ranges reduce the sequence length seen by the planning passes. They
also repeat palette training, boot preloads and player startup. Extraction
decodes the original prefix before trimming, preserving temporal filter and
audio-filter context; later parts therefore retain linear input-reading work.
Multipart is not a guarantee that a short source encodes faster overall.
Resolution, cadence, cold cap and filter/palette policy remain fixed, while
independent palette and quality decisions can change the resulting pixels.

The Sonic Jam OP three-part qualification uses 2,714 source frames at 288x200,
30 fps and cold cap 210. With e195, four CPU workers and SSD-backed workspace
arrays, measured process wall times were:

| Part | Source frames (half-open) | Encode | Pack/verify |
|---|---|---:|---:|
| 1 | `[0, 905)` | 146.0 s | 11.3 s |
| 2 | `[905, 1810)` | 162.0 s | 17.6 s |
| 3 | `[1810, 2714)` | 154.0 s | 11.7 s |

The summed encode/pack time is 502.6 s, excluding ISO assembly. Part 2 was
encoded first, then reused while completing the others. These CPU measurements
are not a controlled comparison against a single full-source encode. The DEBUG
ISO trace verifies all 2,714 frames plus another complete part 1 (3,619 displayed
frames), with exact SIM pixel matches and zero recovery counters. The release
ISO also completes numbered dispatch and loops in the managed emulator. Physical
hardware and boundary audio quality are not qualified by these traces.

## Measured Time

The following measurement is a versioned historical reference taken at
encoder version `20260725.e124`, before automatic hardware-scroll adoption
existed: H40, 288x200 pixels, 30 fps, 2,714 frames, and GPU quantization on
an NVIDIA GeForce RTX 4090. It is a forced full encode with no cached
simulation artifact. This 36x25-cell geometry lacks the full-width 40-column
grid that hardware-scroll adoption requires, so its Quantize and Decide times
contain no scroll detection or guard work.

| Stage | Time | Time per frame |
|---|---:|---:|
| Extract | 7.3 s | 2.7 ms |
| Palette | 12.9 s | 4.7 ms |
| Quantize | 8.6 s | 3.2 ms |
| Forecast | 72.7 s | 26.8 ms |
| Decide | 114.3 s | 42.1 ms |
| Finalize | 0.9 s | 0.3 ms |
| **Total** | **216.9 s** | **79.9 ms** |

### Normalized-input planning comparison

The complete Sonic Jam OP profile (2,714 frames, 288x200, 30 fps, cold cap 210)
was compared on a Core i9-13900KF with common normalized RGB/PCM input and
independent palette training, quantization and decisions. The native run used
one CPU worker with GPU acceleration disabled; the browser used Chromium and
Pyodide. Both retain encoder/player version `20261020.e194.p157` and identical
output. The reference source is `f751c994fe4ac7f78a3619ac9fc29705bc989d7e`.

| Runtime | Before reuse | Complete reuse | Encode time reduction |
|---|---:|---:|---:|
| Native Python, CPU, one worker | 462.0 s | 411.7 s | 10.9% |
| Browser WebAssembly | 1006.3 s | 856.2 s | 14.9% |

Each timing is one execution. Normalization, runtime download, packing, MP4,
ISO and ZIP creation are excluded. These are encoder measurements on prepared
inputs, not ordinary-input Generate latency or GPU CLI throughput. Every
HEADER/BODY/MOVIE byte, every canonical RGB frame and reconstructed PCM matched.
`webenc/harness/planning/README.md` records the intermediate measurements,
stage variation, environment and reproduction procedure.

### Native Windows full-source sample

This is a host-specific qualification sample, not a cross-platform reference:
the generated flat-color 16,384-frame source is 320x224 at 24 fps, uses
mosaic-gm and cold cap 210, and ran on Windows 10 (build 19045) with an i7-9700 and RTX 5060
Ti. Array storage was disk-backed for CPU/CUDA and memory-backed for the third
run. Stage times are one run each; the second CUDA column is a fresh resource
measurement rerun:

| Stage | CPU, disk | CPU, memory | CUDA, disk | CUDA, rerun |
|---|---:|---:|---:|---:|
| Extract | 29.9 s | 29.1 s | 29.9 s | 29.2 s |
| Palette | 123.3 s | 109.9 s | 133.4 s | 119.1 s |
| Quantize | 364.0 s | 357.3 s | 235.3 s | 218.4 s |
| Forecast | 135.4 s | 134.2 s | 132.5 s | 132.8 s |
| Decide | 1,045.9 s | 1,027.3 s | 975.2 s | 972.0 s |
| Finalize | 0.8 s | 0.8 s | 0.8 s | 0.8 s |
| **Total** | **1,699.3 s** | **1,658.6 s** | **1,507.1 s** | **1,472.4 s** |

All saved CPU/CUDA decisions, traces, palettes, masks and audio matched. The
memory-backed CPU result also matched disk-backed CPU. Decide remains CPU work,
so CUDA is not a GPU-only encode. The fresh CUDA rerun matched the earlier CUDA
output across all saved decisions, numeric traces, palettes, masks and audio;
comparison normalized only profile-file and source-path provenance.

The five-second process-tree sample measured a 5.34 GiB summed Working Set
peak and a 1.53 GiB summed `Get-Process.PrivateMemorySize64` peak. Thirty-second
directory samples measured 4.80 GiB peak scratch and 87.7 MiB peak workspace.
Five-second `nvidia-smi` samples measured 1,162 MiB device-wide GPU use; idle
use after the run was 756 MiB, so this is not process-attributed memory. These
sampled peaks can miss brief spikes. The earlier 7.38 GiB value used
`Win32_Process.PrivatePageCount`, not Private Bytes.

All five packed outputs also match the recorded Linux 8cc7bea hashes, though
the documented Linux and Windows MP4 source hashes differ and the Linux input
is not available here. This does not establish a strict same-normalized-input
comparison. Broader source/profile coverage remains open; see
`harness/windows_port/README.md`.

### Native Windows real-source profile sample

The repository's Tears of Steel profile was run against the first 116.564 s of
the official [Blender 720p source](https://download.blender.org/demo/movies/ToS/tears_of_steel_720p.mov)
(SHA-256 `efa9062d9cdb7a338e40ad530dfdf234806743f29ae6a1a136b97ece4e588e8f`).
Blender identifies the film as CC BY 3.0 on its [project page](https://mango.blender.org/about/).
The profile settings were preserved: 320x168, 24 fps, mosaic-gm, cold caps
`2:180,3:230`, and its mono audio filter. The 2,798-frame CUDA encode ran in a
fresh isolated pinned GPU environment on the Windows 10 / RTX 5060 Ti host.

| Stage | 15 fps, stl4 | 24 fps, mosaic-gm | 30 fps, mosaic-gm | 30000/1001 fps, mosaic-gm |
|---|---:|---:|---:|---:|
| Extract | 10.0 s | 16.8 s | 16.7 s | 16.7 s |
| Palette | 47.5 s | 39.0 s | 35.5 s | 34.8 s |
| Quantize | 65.2 s | 134.8 s | 140.0 s | 142.9 s |
| Forecast | 19.4 s | 29.4 s | 33.1 s | 33.1 s |
| Decide | 102.5 s | 228.2 s | 292.5 s | 293.0 s |
| Finalize | 8.2 s | 10.1 s | 9.2 s | 8.5 s |
| **Total stage time** | **252.8 s** | **458.2 s** | **526.9 s** | **529.0 s** |

Palette training selected three segments; fade-in and horizontal scroll were
detected. WordBuf remained enabled through the full preload/refill proof. The
normal packer verified all 2,798 display slots, pattern transfers, audio chunk
order, routes and WordBuf deadlines. Native Main/Sub/boot assembly and the
independent ISO reader produced a verified JP release ISO/CUE in 73.4 s; the
ISO is 18,671,616 bytes. Its SHA-256 is
`2f956affbd553b82a0c9f7ce412304a36b72d2b14fdad4d8417fa624dc4bc1be`.

A second profile variant used the same source clip at 15 fps with the `stl4`
palette and cold cap 480. Its 1,749-frame encode completed in 255.5 s CLI wall
time, and the 15-fps package also passed full display, pattern-transfer,
WordBuf, audio, cadence and physical-slot verification. Native player assembly
and independent ISO verification completed in 37.3 s. The 18,636,800-byte ISO
and its CUE are preserved under
`tmp/windows-qual-tears-of-steel-stl4-15/`; its SHA-256 is
`ca86601dccf63671855800f4c1a7da097f415c905fa7d40879e99eda9dc7e530`.
A third real-source variant used 30 fps, mosaic-gm and cold cap 210. Its 3,498
frames completed in 529.6 s CLI wall time (526.9 s stage time). Packing
verified all display slots, pattern transfers, audio chunks, cadence routes
and WordBuf deadlines; native player assembly and independent ISO verification
completed in 99.4 s. Its 18,692,096-byte ISO/CUE are preserved under
`tmp/windows-qual-tears-of-steel-30fps/`; SHA-256 is
`2f109ff954cd7e2d5e1ae2a2c61f3a392ef767e76ece13675c85bf0a737acc89`.
Together, these runs cover both palette algorithms and the 15/24/30 fps cadence
families on real footage. They are not playback qualifications or broad source
profile benchmarks.

A fourth variant used the exact project cadence `30000/1001` (29.97 fps),
mosaic-gm and cold cap 210. Its 3,494-frame CUDA encode took 531.8 s CLI wall
time (529.0 s stage time). The packer verified every display slot, pattern
transfer, audio chunk, cadence route and WordBuf deadline; native player/ISO
assembly and independent ISO verification completed in 102.0 s. The 18,669,568-
byte ISO/CUE are preserved under `tmp/windows-qual-tears-of-steel-2997/`; its
SHA-256 is `a778ae48d7f84e4ffba97fc05329ecc19f4446f051fdf708472a27dc21265023`.
Receipts are `logs/windows-cli-encode-20261030T185819361995Z.json` and
`logs/windows-cli-disc-20261030T185822110872Z.json`. The documented
`tools/cavc.ps1 convert` command then reused the same completed 3,494-frame
encode (`simulation skipped`) and rebuilt the verified ISO in 102.0 s through
the argument-preserving PowerShell launcher.
Together, the real-source
runs now cover 15, 24, 30 and 30000/1001 fps cadence families with both palette
algorithms. They remain encode/pack/ISO qualifications, not playback proof or
broad coverage of other source profiles.

## Planning work reuse

The online delivery planner checks only the preceding and newly appended
slots when committing cumulative control/payload prefixes. It still checks
finite PrgBuf delivery for each trial and performs a complete prefix proof
at the end. Trials in the same frame reuse feasibility only when both
cumulative sector counts match; detailed byte counts and margins are not
cached under that key. Cadence, committed control sectors and cumulative
Prg/control amounts are retained; each trial replaces only the current
frame's inputs and cumulative values. Trials omit diagnostic arrays that
the caller does not use. Backward deadline propagation and finite-ring
delivery still run for every trial, so repeated prefix replay retains a
quadratic component in movie length.

WordBuf planning maintains undelivered potential Prg demand through delivery,
source assignment and rollback, skips pressure forecasts when no WordBuf
sector can fit, and scans candidates only through the required complete
frame horizon. Per-frame non-Word demand is updated when sources change,
including rollback; already delivered Prg patterns remain in that frame's
consumption demand. The layout loop shares item/frame/parity indexes, merged
runs and initial control/cadence arrays across capacity trials. Each trial
owns its mutable boot selection, source, delivery and occupancy state.
Compact and ring-split descriptor counts share one run construction per
frame. Subset scores, candidate order and physical replay remain unchanged.

Quality-reserve searches reuse constant demand differences and
resume from the last changed busy period. Empty boot-prefetch plans reuse
the baseline prefetch forecast; fallback comparisons reuse the current
display error within one decision.

`harness/encoder_planning/compare.py` compares the actual reference and current
planners in separate processes, including per-frame limits, complete routes,
WordBuf sources, reserves and dictionary order. Its default 512-frame
synthetic workload measures planning components, not full-encode throughput.
The end-to-end output and memory qualification remains the separate
16,384-frame source procedure in `harness/runtime_storage/README.md`.

---

<a id="jp"></a>

# シミュレーション・エンコード工程

この文書は `tools/sim.py` の処理フローと実測時間を説明します。ディスクへの
パックと解析レンダリングは、この計測には含みません。

## 処理フロー

```text
Extract -> Palette -> Quantize -> Forecast -> Decide -> Finalize
```

| 工程 | 処理 |
|---|---|
| **Extract** | ソースをエンコーダ用フレーム、比較用フレーム、モノラル音声へ展開します。 |
| **Palette** | 映像をパレット区間に分け、区間ごとのCRAMパレットを学習し、sourceのtime指定なしで片側または両側が黒に接するfadeのうち、空間的に静止したshotを自動検出します。 |
| **Quantize** | 全フレームをパレット割り当てとインデックス形式の8x8パターンへ変換し、sourceのtime指定なしでハードウェアscroll区間を自動検出して採用windowと各frameのplane状態を決め、windowごとのworld-mosaic guard辺を量子化します。選択したfadeは1枚の正確なreference imageを固定してinline CRAMの段階を決めます。 |
| **Forecast** | 将来の需要、物理配信上限、画質用の予約量、起動時プリロード利用（frame 0のinline staging領域とboot-VRAM sidecarのbacksideへの振り分けを含む）、片側fade-outに必須のreference prefetch容量を1パスで計算します。 |
| **Decide** | 各フレームの正確パターンまたは再利用パターンを選び、最後のname-table DMAまで直前表示のslotを維持し、一時的な旧表示と新表示のpattern和集合がVRAMを越える選択を退けてslotを割り当て、片側fade referenceのresident分を維持しながらcold preloadを端の連続blockへ集約して回収されたentryを取り戻し、採用したscroll frameではguard辺の供給分を予約して単独runを前提にcoldとPrgの上限をクランプし、パターン供給元と物理予算を確定します。準備deadlineを完了できないfadeはそのshotを通常frameへ降格し、そのfadeを除いてencodeを再実行します。 |
| **Finalize** | packと同一の共有sector経路でshadow update listを計画し、scroll frameのrolling plane分のname-table wordを加算したうえで、全編の物理スケジュールを検証し、数値ログと決定ログを書き出します。 |

## 作業メモリ

`--array-storage disk` はframe特徴量と量子化後の数値cacheを、指定したscratch rootの
fileへmapします。`memory` は通常の配列を使います。frame特徴量の読み込みは、待機task数を
制限します。edge-strength dataはpalette学習後、source特徴量のviewは量子化後、音声の
中間dataは出力後、baselineの需要予測・prefetch objectはboot計画後に解放します。
最終decisionには後工程で必要な数値予測を保持し、重複したpattern-key sequenceは残しません。

encoder設定と数値結果は維持します。disk modeにはI/O待ち時間とディスク容量が必要で、
Pythonのpattern履歴やdecision logの増加までは制限しません。scratchがtmpfsなら物理RAMを
使います。`harness/runtime_storage/measure.py` でprocess RSS、process treeのsampled RSS、
system shared memory、filesystem使用量を区別できます。全工程のsource比較には
320x224・16,384フレームを使い、固定した親commitと現在のencoderの出力を照合します。
動きの少ない入力なので、stage cacheの保持は計測できますが、複雑な映像のpattern履歴の
最大量を測るものではありません。

## Multipartの独立した計画処理

CLIの`tools/multipart.py`は、`--parts N`を優先し、省略時はprofileの`[multipart].parts`から
分割数を決めます。profileの出力frame clockで素材のframe数を数え、連続した
範囲に分けて、それぞれでencoderとpackerの全工程を実行します。palette、
whole-movie quality budget、物理配送スケジュールはpartごとに独立します。
任意の順序でpartをエンコードでき、完成済みpartは計画処理を繰り返さずに再利用します。
全partが必要になるのは最後のISO組み立てです。ブラウザ版のworkflowは追加しません。

範囲を短くすることで計画処理が扱うsequence長を減らします。一方でpalette学習、
boot preload、player起動はpartごとに繰り返します。抽出は元素材の先頭からdecodeして
trimするため、時間方向のfilterと音声filterの文脈が保たれますが、後半のpartでも
入力を読む線形の処理は残ります。短い素材の総エンコード時間が必ず減る機能ではありません。
解像度、cadence、cold cap、filter/palette policyを維持しても、paletteと画質の判断が
独立するため、生成されるpixelは変わる場合があります。

Sonic Jam OPの3分割検証は、2,714 source frames、288x200、30 fps、cold cap 210を
使っています。e195、CPU worker 4つ、SSD上のworkspace配列で計測したprocessの
wall timeは次のとおりです。

| Part | Source frames（半開区間） | Encode | Pack/verify |
|---|---|---:|---:|
| 1 | `[0, 905)` | 146.0 s | 11.3 s |
| 2 | `[905, 1810)` | 162.0 s | 17.6 s |
| 3 | `[1810, 2714)` | 154.0 s | 11.7 s |

encode/packの合計は502.6秒で、ISO組み立ては含みません。part 2を先にエンコードし、
残りの処理では再利用しました。これはCPUでの実測であり、全素材を一度にエンコードする
方式との条件を揃えた比較ではありません。DEBUG ISOのtraceでは全2,714 framesと
もう一度のpart 1全編、計3,619表示framesがSIMのpixelと完全一致し、復旧counterは
ゼロでした。release ISOも管理対象emulatorで番号順の切り替えとループを完了します。
このtraceでは実機と境界の音質は検証していません。

## 実測時間

次の計測は、エンコーダ版 `20260725.e124` 時点（ハードウェアscroll自動採用の
実装より前）の版数付き参考値です。H40、288x200ピクセル、30 fps、2,714フレーム、
NVIDIA GeForce RTX 4090によるGPU量子化を使用しています。既存のsim成果物を
再利用しない全編エンコードです。この36x25 cellの解像度はscroll採用に必要な
全幅40列gridを持たないため、QuantizeとDecideの時間にscroll検出やguard処理は
含まれません。

| 工程 | 時間 | 1フレームあたり |
|---|---:|---:|
| Extract | 7.3秒 | 2.7 ms |
| Palette | 12.9秒 | 4.7 ms |
| Quantize | 8.6秒 | 3.2 ms |
| Forecast | 72.7秒 | 26.8 ms |
| Decide | 114.3秒 | 42.1 ms |
| Finalize | 0.9秒 | 0.3 ms |
| **合計** | **216.9秒** | **79.9 ms** |

### 正規化済み入力による計画処理の比較

Sonic Jam OPの全profile（2,714フレーム、288x200、30 fps、cold cap 210）を、
Core i9-13900KFで比較しました。正規化済みRGB/PCMを共通入力とし、パレット学習・
量子化・decisionは独立に計算しています。nativeはGPUを無効にしたCPU 1 worker、
ブラウザはChromiumとPyodideを使用しました。両版ともencoder/player版は
`20261020.e194.p157`で、出力は同一です。変更前のsourceは
`f751c994fe4ac7f78a3619ac9fc29705bc989d7e`です。

| 実行環境 | 再利用の実装前 | 再利用の全項目実装後 | エンコード時間の短縮率 |
|---|---:|---:|---:|
| Native Python、CPU 1 worker | 462.0秒 | 411.7秒 | 10.9% |
| ブラウザ WebAssembly | 1006.3秒 | 856.2秒 | 14.9% |

各時間は1回の実行結果です。正規化、runtimeのdownload、pack、MP4・ISO・ZIP生成は
含めません。前処理済み入力に対するencoderの計測であり、通常入力でGenerateを
押してからの時間や、GPUを使ったCLIの処理速度は測っていません。HEADER/BODY/MOVIEの
全byte、全canonical RGB frame、再構成PCMの一致を確認しました。
`webenc/harness/planning/README.md`に中間段階の測定、工程別の変動、環境と再現手順を
記載しています。

### Native Windowsでの全source測定

これはhost固有のqualification sampleであり、cross-platform比較の基準では
ありません。mosaic-gm、cold cap 210の生成flat-color sourceは16,384 frames、
320x224、24 fpsです。Windows 10 (build 19045)、i7-9700、RTX 5060 Tiで測定し、CPU/CUDAは
disk-backed array、3回目のCPU実行はmemory-backed arrayを使いました。各stageは1回の測定値で、
最後のCUDA列はresource測定のための再実行です。

| Stage | CPU、disk | CPU、memory | CUDA、disk | CUDA、再実行 |
|---|---:|---:|---:|---:|
| Extract | 29.9 s | 29.1 s | 29.9 s | 29.2 s |
| Palette | 123.3 s | 109.9 s | 133.4 s | 119.1 s |
| Quantize | 364.0 s | 357.3 s | 235.3 s | 218.4 s |
| Forecast | 135.4 s | 134.2 s | 132.5 s | 132.8 s |
| Decide | 1,045.9 s | 1,027.3 s | 975.2 s | 972.0 s |
| Finalize | 0.8 s | 0.8 s | 0.8 s | 0.8 s |
| **合計** | **1,699.3 s** | **1,658.6 s** | **1,507.1 s** | **1,472.4 s** |

CPU/CUDA間ですべての保存decision、trace、palette、mask、audioが一致しました。
memory-backed CPUもdisk-backed CPUと一致しました。DecideはCPU処理のため、CUDA実行は
GPUのみのencodeではありません。新しいCUDA再実行も以前のCUDA出力と一致し、profile fileと
source pathのprovenanceだけを正規化した比較で、全decision、numeric trace、palette、mask、
audioが一致しました。

5秒間隔のprocess tree測定では、Working Set合計の最大は5.34 GiB、
`Get-Process.PrivateMemorySize64`合計の最大は1.53 GiBでした。30秒間隔のdirectory測定では、
scratch最大4.80 GiB、workspace最大87.7 MiBでした。5秒間隔の`nvidia-smi`測定では
device全体の使用量が最大1,162 MiBで、実行後のidle使用量は756 MiBでした。このGPU値は
process単位の使用量ではありません。sampled peakのため短時間のspikeを逃す場合があります。
以前の7.38 GiBは`Win32_Process.PrivatePageCount`による値で、Private Bytesとは比較できません。

packed output 5種は記録済みのLinux 8cc7bea hashとも一致しますが、Linux/WindowsのMP4 source
hashは異なり、Linux inputもここにはありません。この比較は同じnormalized inputでの厳密な
確認ではありません。他のsource/profileの検証も未完了です。`harness/windows_port/README.md`を参照してください。

### Native Windowsのreal-source profile測定

repositoryのTears of Steel profileを、公式[Blender 720p source](https://download.blender.org/demo/movies/ToS/tears_of_steel_720p.mov)
の先頭116.564秒に対して実行しました（SHA-256 `efa9062d9cdb7a338e40ad530dfdf234806743f29ae6a1a136b97ece4e588e8f`）。
Blenderは[project page](https://mango.blender.org/about/)でfilmをCC BY 3.0としています。
profileの設定は維持し、320x168、24 fps、mosaic-gm、cold cap `2:180,3:230`、既存のmono audio filterを使用しました。
2,798 frameのCUDA encodeはWindows 10 / RTX 5060 Ti host上の新しいpinned GPU environmentで完了しました。

| Stage | 15 fps、stl4 | 24 fps、mosaic-gm | 30 fps、mosaic-gm | 30000/1001 fps、mosaic-gm |
|---|---:|---:|---:|---:|
| Extract | 10.0 s | 16.8 s | 16.7 s | 16.7 s |
| Palette | 47.5 s | 39.0 s | 35.5 s | 34.8 s |
| Quantize | 65.2 s | 134.8 s | 140.0 s | 142.9 s |
| Forecast | 19.4 s | 29.4 s | 33.1 s | 33.1 s |
| Decide | 102.5 s | 228.2 s | 292.5 s | 293.0 s |
| Finalize | 8.2 s | 10.1 s | 9.2 s | 8.5 s |
| **Stage合計** | **252.8 s** | **458.2 s** | **526.9 s** | **529.0 s** |

palette trainingは3 segmentを選び、fade-inとhorizontal scrollを検出しました。全preload/refill proofを通してWordBufは有効でした。
通常packerは2,798 display slot、pattern transfer、audio chunk順、route、WordBuf deadlineを検証しました。
native Main/Sub/boot assemblyと独立ISO readerにより、JP release ISO/CUEを73.4秒で生成・検証しました。ISOは18,671,616 bytes、SHA-256は
`2f956affbd553b82a0c9f7ce412304a36b72d2b14fdad4d8417fa624dc4bc1be`です。

2つ目のprofile variantは同じsource clipを15 fps、`stl4` palette、cold cap 480で処理しました。1,749 frameのencodeはCLI wall time 255.5秒で完了し、
15 fpsのpackageもdisplay、pattern transfer、WordBuf、audio、cadence、physical slotの完全検証を通過しました。native player assemblyと独立ISO検証は37.3秒で完了しました。
18,636,800-byteのISO/CUEは`tmp/windows-qual-tears-of-steel-stl4-15/`に保存し、SHA-256は
`ca86601dccf63671855800f4c1a7da097f415c905fa7d40879e99eda9dc7e530`です。
3つ目のreal-source variantは30 fps、mosaic-gm、cold cap 210です。3,498 frameのencodeはCLI wall time 529.6秒（stage time 526.9秒）で完了しました。
packは全display slot、pattern transfer、audio chunk、cadence route、WordBuf deadlineを検証し、native player assemblyと独立ISO検証は99.4秒で完了しました。
18,692,096-byteのISO/CUEは`tmp/windows-qual-tears-of-steel-30fps/`に保存し、SHA-256は
`2f109ff954cd7e2d5e1ae2a2c61f3a392ef767e76ece13675c85bf0a737acc89`です。
4つ目のvariantはprojectと同じ`30000/1001`（29.97 fps）、mosaic-gm、cold cap 210を使用しました。3,494 frameのCUDA encodeはCLI wall time 531.8秒（stage time 529.0秒）でした。packは全display slot、pattern transfer、audio chunk、cadence route、WordBuf deadlineを検証し、native player/ISO assemblyと独立ISO検証は102.0秒で完了しました。18,669,568-byte ISO/CUEは`tmp/windows-qual-tears-of-steel-2997/`に保存し、SHA-256は`a778ae48d7f84e4ffba97fc05329ecc19f4446f051fdf708472a27dc21265023`です。receiptは`logs/windows-cli-encode-20261030T185819361995Z.json`と`logs/windows-cli-disc-20261030T185822110872Z.json`です。ドキュメント記載の`tools/cavc.ps1 convert`も同じ3,494-frame encodeをcacheから再利用し（simulation skipped）、更新後のPowerShell launcherで検証済みISOを102.0秒で再構築しました。

これらのreal-source実行は両palette algorithmと15、24、30、30000/1001 fps cadence familyを確認しました。encode/pack/ISO qualificationであり、playback proofや他source profileの幅広い検証ではありません。

## 計画処理の再利用

逐次配送plannerは、累積control/payloadの確定時に直前slotと新しく追加したslotを
検査します。各候補の有限PrgBuf配送検査と、終了時の全prefix検証は維持します。
同じframe内では、累積sector数の組が両方一致する場合に可否判定を再利用します。
詳細なbyte数やmarginは、このkeyでは再利用しません。cadence、確定済みcontrol sector、
Prg/controlの累積量を保持し、候補評価では現在のframeの入力と累積値だけを更新します。
呼び出し元が使わない診断用配列の生成は省きます。締切の逆向き伝播と有限PrgBufの
配送計算は各候補で引き続き行うため、prefix全体の再計算には動画長に対して二次の
計算量となる部分が残ります。

WordBuf計画は、未配送でPrg供給となり得る需要を、配送・供給元の割当・rollbackに
合わせて更新します。WordBufに1sectorも配送できない場合は不足予測を省き、
候補走査は必要なframeまでに限定して、そのframe内の候補はすべて含めます。
frame別の非Word需要は供給元の変更とrollbackに合わせて更新し、配送済みのPrgも
そのframeの消費需要に含めます。レイアウト反復ではitem/frame/parity列、merged run、
初期control/cadence配列を容量試行間で共有します。boot選択、供給元、配送量、残量の
可変状態は試行ごとに独立させます。分割前とring境界分割後のdescriptor数は、各frameで
一度作ったrunから計算します。subsetのscore、候補順、物理replayは維持します。

画質予算の探索は不変の需要差を
再利用し、最後に変更した連続不足区間の先頭から再開します。boot-prefetch計画が
空の場合はbaselineのprefetch予測を再利用し、fallback比較では1回のdecision内で
現在表示の誤差を再利用します。

`harness/encoder_planning/compare.py` は、変更前後の実際のplannerを別processで実行し、
frame別上限、全route、WordBuf供給元、予約量、辞書順を比較します。既定の512フレームの
合成入力は計画処理単体の測定であり、全編encodeの処理速度は測りません。
全工程の出力一致とメモリ検証は、`harness/runtime_storage/README.md` の
16,384フレームのsourceを使う別手順で行います。
