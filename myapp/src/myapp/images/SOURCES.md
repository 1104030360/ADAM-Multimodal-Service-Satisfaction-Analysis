# 圖片的來源與授權

這個資料夾的圖片是 2026-10-31 補上的。原本的圖片從來沒有進過 git（`.gitignore` 忽略了所有 `*.png`、`*.jpg`），clone 下來的 app 找不到它們，啟動時會印出 `Cannot open`。這次的主題是灰黑、科技感。

| 檔案 | 用在哪裡 | 來源 | 授權 |
|---|---|---|---|
| `logo.jpg` | 開場畫面（`login.qml`） | 下面那張照片，加上原創的標誌和文字 | 照片：Unsplash License；其餘：本專案原創 |
| `ADAM_login.jpg` | 登入畫面的背景（`login.qml`） | Jorge Salvador 在 Unsplash 的照片，裁切並調暗 | Unsplash License |
| `icons/user.png` | 側邊欄的頭像（`server_home.qml`、`admin_home.qml`） | Lucide 1.58.0 | ISC。全文在 `icons/LICENSE-lucide.txt`，必須和圖片放在一起 |

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

九個圖檔都## 側邊欄的圖示與頭像

都由 [Lucide](https://lucide.dev)（https://github.com/lucide-icons/lucide）的圖示產生：npm 套件 `lucide-static` 1.58.0，授權 ISC。

- SVG 下載自 `https://cdn.jsdelivr.net/npm/lucide-static@1.58.0/icons/<圖示名稱>.svg`。
- 另外下載了官方的 npm tarball，它的 sha512 和 npm registry 記錄的相符；用到的 SVG 和 `LICENSE` 都和 tarball 裡的逐位元組相同。
- 2026-10-31 先做成 `icons/` 底下 8 個 PNG。2026-11-01 前端重建 F5（T23）把側邊欄改成 `SidebarButton`，按鈕圖示換成下面的 SVG，那 8 個 PNG 刪掉了；`icons/` 只剩頭像。

### 按鈕圖示（`myapp/src/myapp/qml/components/icons/nav/`，15 個 SVG）

| Lucide 圖示 | 按鈕 | 選用理由 |
|---|---|---|
| `house` | Home | |
| `video` | Record（服務人員端） | 錄影 |
| `brain-circuit` | Analysis（服務人員端） | AI 分析 |
| `history` | Services | 查詢過去的服務 |
| `file-chart-column` | Reports | 帶長條圖的文件 |
| `settings` | Settings | |
| `user-round-plus` | Add Account（管理員端） | 新增帳號；人形和頭像一樣是圓的 |
| `log-out` | Quit | |

- **檔名**：`<Lucide 名稱>_<Theme token>.svg`，規則和下一節的共用元件圖示一樣；`tests/unit/test_qml_components.py` 一起檢查（顏色等於 token、畫得出來、每個都有用到）。
- **兩種顏色**：目前頁面的按鈕是亮底，所以每個圖示有兩份。Quit 不會是目前頁面，只有亮色那一份。
  - `chromeIcon` `#E6E8EB`：一般、hover、按下。對比在側邊欄 `#333333` 上 10.29:1、按鈕 `#444444` 上 7.93:1、hover `#555555` 上 6.07:1、按下 `#666666` 上 4.68:1。
  - `navItemCurrentText` `#0E1013`：目前頁面（亮底 `#E6E8EB`）上 15.5:1。
- **修改**（ISC 允許）：只把 `stroke="currentColor"` 換成固定的顏色，其餘位元組和原檔相同。把顏色換回 `currentColor`，15 個檔案的 SHA-256 都和下面記的來源相同（2026-11-01 驗證過）。
- Lucide 的 `LICENSE` 把 `log-out` 列為衍生自 Feather 的圖示，所以同一份授權檔裡 Feather 的 MIT 授權（Copyright (c) 2013-present Cole Bemis）也適用於 `log-out_chromeIcon.svg`。
- 放在 `nav/` 子資料夾，不和共用元件的 15 個圖示混在一起。

### 頭像（`icons/user.png`）

- 由 `circle-user-round` 產生（ISC 允許修改）：拿掉外圈，只留頭和肩膀；後面的深色圓盤、間隙和外環是另外畫的，不是取自 Lucide。人形是 `#E6E8EB`。
- 200×200 的透明 PNG，QML 以 100×100 顯示，等於 Retina 螢幕的 2 倍圖。炭灰色的圓盤（`#30343A` → `#15171A`）外面留 3 px 透明間隙，再一圈石墨色的外環（`#8C929A` → `#43474E`）。

t
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

## 共用元件的圖示（`qml/components/icons/`）

前端重建 F1 的共用元件（通知、狀態標籤、忙碌按鈕、錯誤與空狀態）用的 15 個圖示，2026-10-31 加入。它們放在 `myapp/src/myapp/qml/components/icons/`，不在這個資料夾，但來源和授權一起記在這裡。

- **來源**：[Lucide](https://lucide.dev) 1.58.0，和上面的側邊欄圖示是同一個官方 npm tarball `lucide-static-1.58.0.tgz`（integrity `sha512-VFE2ZEq3yEwjBH0El9CA9lHgDWKKXPKgKxFzX+/JN1FPa4maQbtl/pT4B62aJkUEpODT5octDsyGy7tPVE2svQ==`）。用 `tar` 直接從 tarball 取出，沒有另外下載。
- **授權**：ISC。全文在 `icons/LICENSE-lucide.txt`，和 tarball 裡的 `LICENSE` 逐位元組相同。每個 SVG 都保留了原本第一行的 `<!-- @license lucide-static v1.58.0 - ISC -->`。
  - `info`、`x`、`clock`、`upload`、`circle-x`、`circle-alert`、`triangle-alert` 是 Lucide `LICENSE` 列出的 Feather 衍生圖示（列在舊名 `x-circle`、`alert-circle`、`alert-triangle` 底下），同一份授權檔裡 Feather 的 MIT 授權也適用。
- **修改**（ISC 允許）：只把 `stroke="currentColor"` 換成固定的顏色，其餘位元組和原檔相同。顏色在檔案裡預先上好，不在執行時染色（CONTRACTS §2：避免 layer 的成本）。
- **格式**：SVG，不轉成 PNG。Qt 的 SVG 外掛（`libqsvg`，PySide6 6.12 內建）畫得出這 15 個檔案，沒有任何警告；`Image` 設了 `sourceSize` 時會依螢幕的 devicePixelRatio 重新點陣化，Retina 上也是清楚的。`tests/unit/test_qml_components.py` 會檢查：畫得出來、沒有警告、畫出來的線條就是檔名寫的那個 Theme 顏色。
- **檔名**：`<Lucide 名稱>_<Theme token>.svg`。token 就是 `Theme.qml` 的屬性名稱；測試會比對檔案裡的顏色和那個 token 的值。改了 `Theme.qml` 的顏色，要重新產生對應的圖示。

| 檔案 | 用在 | 顏色 |
|---|---|---|
| `circle-check_successOnDark.svg` | 通知：成功 | `successOnDark` `#81C784` |
| `info_infoOnDark.svg` | 通知：資訊 | `infoOnDark` `#90CAF9` |
| `triangle-alert_warningOnDark.svg` | 通知：警告 | `warningOnDark` `#FFB74D` |
| `circle-alert_errorOnDark.svg` | 通知：錯誤 | `errorOnDark` `#FF9A90` |
| `upload_chromeIcon.svg` | 通知：進行中 | `chromeIcon` `#E6E8EB` |
| `x_chromeIcon.svg` | 通知的關閉鈕 | `chromeIcon` `#E6E8EB` |
| `circle-dashed_textPrimary.svg` | 狀態標籤：Not Analyzed | `textPrimary` `#333333` |
| `clock_infoOnLight.svg` | 狀態標籤：Analyzing | `infoOnLight` `#1565C0` |
| `circle-check_successOnLight.svg` | 狀態標籤：Completed | `successOnLight` `#2E7D32` |
| `circle-x_errorOnLight.svg` | 狀態標籤：Failed | `errorOnLight` `#C62828` |
| `circle-alert_errorOnLight.svg` | 區塊錯誤（20 px）、欄位錯誤（14 px） | `errorOnLight` `#C62828` |
| `inbox_textDisabled.svg` | 空狀態（40 px，裝飾） | `textDisabled` `#8E959F` |
| `loader-circle_textOnDark.svg` | 忙碌按鈕：primary | `textOnDark` `#FFFFFF` |
| `loader-circle_textSecondary.svg` | 忙碌按鈕：secondary、danger | `textSecondary` `#596069` |
| `loader-circle_chromeOnLight.svg` | 忙碌按鈕：chrome（深色背景上的亮色按鈕） | `chromeOnLight` `#0E1013` |

來源 SVG 的 SHA-256（上色之前）：

```text
136cb1a270216ccd678be230acbab73771349372b8ecb9283116528d5c4ae269  circle-alert.svg
e963fb03e3180118ca0b99fba7cc9644769dc61a35280e3da75fc3a65fd450c4  circle-check.svg
2ea5a8a81920485da3eb70bdc8e2c349df0311fd7888ba0e9d85dcac0db3b512  circle-dashed.svg
2b16f7f66feba8412175c1b3c45f793cb603f803b6c9de6daa16a7a928850cbd  circle-x.svg
863f184bbb4460fd133fb3da9b84b9158017d6af5b187c9f09b79cae0d1f972d  clock.svg
7d54bce88627817f1eafb17b4bb79ea2ee444c6a661b843afc49e731b859ccd8  inbox.svg
bd039cbc60abc0af2d241232fa6fe76da45c974a8928141b266b15435ca26e0c  info.svg
8ef4c230a13382644c11c1042e120c9cc00196856765bf84bde2362d671d0b50  loader-circle.svg
57b21e3ead643bb1eb69e0eed7fbcd8820b77491124c26b1bb342b0e1079711a  triangle-alert.svg
868173535cd3a65071d2527e5284ad1d1a3144464f8a107fdab47314c2a945e8  upload.svg
4ab0e1e5031230da80a2ddaf9c8639bde234a974a1bc94b7dfbac7af14f55a25  x.svg
```
