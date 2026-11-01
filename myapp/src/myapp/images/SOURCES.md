# 圖片的來源與授權

這個資料夾的圖片是 2026-10-31 補上的。原本的圖片從來沒有進過 git（`.gitignore` 忽略了所有 `*.png`、`*.jpg`），clone 下來的 app 找不到它們，啟動時會印出 `Cannot open`。這次的主題是灰黑、科技感。

| 檔案 | 用在哪裡 | 來源 | 授權 |
|---|---|---|---|
| `logo.jpg` | 開場畫面（`login.qml`） | 下面那張照片，加上原創的標誌和文字 | 照片：Unsplash License；其餘：本專案原創 |
| `ADAM_login.jpg` | 登入畫面的背景（`login.qml`） | Jorge Salvador 在 Unsplash 的照片，裁切並調暗 | Unsplash License |
| `icons/*.png`（9 個） | 側邊欄的圖示與頭像（`server_home.qml`、`admin_home.qml`） | Lucide 1.58.0 | ISC；`exit.png` 另外適用 MIT。全文在 `icons/LICENSE-lucide.txt`，必須和圖片放在一起 |

產生這些圖片的腳本和原始素材只放在這台電腦的 `docs/rebuild/assets/`（被 git 忽略），用它們可以重新產生出逐位元組相同的檔案。

## 照片（`ADAM_login.jpg`，以及 `logo.jpg` 的背景）

| 項目 | 內容 |
|---|---|
| 標題 | "Black and white scale of a computer motherboard" |
| 作者 | Jorge Salvador（[@jsshotz](https://unsplash.com/@jsshotz)） |
| 頁面 | https://unsplash.com/photos/black-and-white-scale-of-a-computer-motherboard-Lq8Mfp3HoEg |
| 授權 | Unsplash License，https://unsplash.com/license |
| 下載的原檔 | 5456×3632，SHA-256 `bb62a146e9f9e2f65372000d83670c3079bcbee8cf2bf8aebb2fa9fc57424c8f` |

- Unsplash License 允許免費用在商業或非商業用途，不必申請，也不一定要標示作者。不允許的是：沒有大幅修改就拿去賣，或收集 Unsplash 的照片做成類似的圖庫服務。把一張修改過的照片放進 app 和它的原始碼，在允許的範圍內。
- 照片裡沒有人、浮水印或商標。唯一看得到的印刷字是中央一顆電容的規格，模糊之後已經看不出來，也被登入面板蓋住。

修改：

- `ADAM_login.jpg`（2048×1536，JPEG 品質 85）：
  - 置中裁成 4:3，再縮小。
  - 整體壓暗，亮部壓低，不會比表單還搶眼。
  - 登入面板底下（畫面中央 40%×60%）模糊並壓暗到 30%，讓表單看得清楚。
  - 四角加上暗角。
- `logo.jpg`（2048×1536，JPEG 品質 90）：同一張照片模糊得更多、壓暗到約 22%，再畫上下一節的原創部分。

## 原創的部分（`logo.jpg`）

除了背景照片，`logo.jpg` 的其他元素都是為這個專案畫的，不是取自既有的標誌。沒有做過商標檢索。

- **標誌**：一排像語音波形的線條（聲音），外形是一隻眼睛（臉）。中間的圓形空白是虹膜，實心圓點是瞳孔。
- **字標**：「ADAM」，下面用國際摩斯電碼再拼一次 ADAM（文字）。
- **其他**：副標「AI SERVICE SATISFACTION ANALYSIS」、左下角的「FACE · VOICE · TEXT」和四角的框線。

字型只是畫進圖片裡，app 沒有附字型檔：

| 字型 | 用在 | 授權 |
|---|---|---|
| Jura Light | 「ADAM」字標 | SIL OFL 1.1，© 2019 The Jura Project Authors（https://github.com/ossobuffo/jura） |
| Geist Mono Regular | 副標、角落的文字 | SIL OFL 1.1，© 2024 The Geist Project Authors（https://github.com/vercel/geist-font） |

## 側邊欄圖示（`icons/`）

九個圖檔都由 [Lucide](https://lucide.dev)（https://github.com/lucide-icons/lucide）的圖示產生：npm 套件 `lucide-static` 1.58.0，授權 ISC。

- SVG 下載自 `https://cdn.jsdelivr.net/npm/lucide-static@1.58.0/icons/<圖示名稱>.svg`。
- 另外下載了官方的 npm tarball，它的 sha512 和 npm registry 記錄的相符；用到的 SVG 和 `LICENSE` 都和 tarball 裡的逐位元組相同。

| 檔案 | 按鈕 | Lucide 圖示 | 選用理由 |
|---|---|---|---|
| `home.png` | Home | `house` | |
| `record.png` | Record | `video` | 錄影 |
| `analysis.png` | Analysis | `brain-circuit` | AI 分析 |
| `service.png` | Service | `history` | 查詢過去的服務 |
| `reports.png` | Reports | `file-chart-column` | 帶長條圖的文件 |
| `setting.png` | Setting | `settings` | |
| `exit.png` | Exit | `log-out` | |
| `add.png` | Add | `user-round-plus` | 新增帳號；人形和頭像一樣是圓的 |
| `user.png` | 頭像 | `circle-user-round`（拿掉外圈） | |

- Lucide 的 `LICENSE` 把 `log-out` 列為衍生自 Feather 的圖示，所以同一份授權檔裡 Feather 的 MIT 授權（Copyright (c) 2013-present Cole Bemis）也適用於 `exit.png`。
- 對原圖的修改（ISC 允許修改）：
  - 所有圖示：把 `stroke="currentColor"` 換成 `#E6E8EB` 再畫成 PNG。
  - `user.png`：拿掉 `circle-user-round` 的外圈，只留頭和肩膀；後面的深色圓盤、間隙和外環是另外畫的，不是取自 Lucide。
- 尺寸與顏色：
  - 8 個按鈕圖示：60×60 的透明 PNG，QML 以 30×30 顯示，等於 Retina 螢幕的 2 倍圖。線條統一是 `#E6E8EB`，對比在側邊欄 `#333333` 上 10.29:1、按鈕 `#444444` 上 7.93:1、hover `#555555` 上 6.07:1、pressed `#666666` 上 4.68:1。
  - `user.png`：200×200，QML 以 100×100 顯示。炭灰色的圓盤（`#30343A` → `#15171A`）外面留 3 px 透明間隙，再一圈石墨色的外環（`#8C929A` → `#43474E`）；人形 `#E6E8EB`。

來源 SVG 的 SHA-256：

```text
dbfcfe7ae82b8d9d092c2165d21aa6b93330f8b4c04e6d5e1b270b905f13df9a  house.svg
898a1b8b815739f79aa95031428c2c11a35dd4b0492029ae407a8881250d489a  video.svg
e6dad1cc4203645e6ac333346a2efb47813fb7dd198add19cb0e886a56a31547  brain-circuit.svg
c40d5f3133df70f9eaf3fdf03234c74995b10b2cc330e2d3b2ad73aeb0cf4409  history.svg
299cfa41fd9008e0c55af2b35d9738894eafa50e2053411a1ad40b57f52990e5  file-chart-column.svg
109df42e03bb5382fc66f3195e30463cc8ff6f2edd8d8597e62c2de862bd0140  settings.svg
595115f16ef08ee211d577e0dab39e448faf9ba85b67ac21704e01656c7724ce  log-out.svg
fc3d8f3566fbce5eeb689eff876627c3c8dc242ff2e5516e3dc0783c3bb56a60  user-round-plus.svg
c7e6ee719aeb7c0e71d6afefa3f8946fcd7232506e881d9666a86f8904ba23bd  circle-user-round.svg
```
