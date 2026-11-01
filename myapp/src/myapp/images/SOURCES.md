# 圖片的來源與授權

這個資料夾的圖片是 2026-10-31 補上的。原本的圖片從來沒有進過 git（`.gitignore` 忽略了所有 `*.png`、`*.jpg`），clone 下來的 app 找不到它們，啟動時會印出 `Cannot open`。這次的主題是灰黑、科技感。

| 檔案 | 用在哪裡 | 來源 | 授權 |
|---|---|---|---|
| `logo.jpg` | 開場畫面（`login.qml`） | 下面那張照片，加上原創的標誌和文字 | 照片：Unsplash License；其餘：本專案原創 |
| `ADAM_login.jpg` | 登入畫面的背景（`login.qml`） | Jorge Salvador 在 Unsplash 的照片，裁切並調暗 | Unsplash License |

`icons/LICENSE-lucide.txt` 是下面介面圖示的授權全文。產生這些圖片的腳本和原始素材只放在這台電腦的 `docs/rebuild/assets/`（被 git 忽略），用它們可以重新產生出逐位元組相同的檔案。

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

## 介面的圖示（`qml/components/icons/`）

共用元件、頁面和側邊欄的 67 個 SVG 圖示。它們放在 `myapp/src/myapp/qml/components/icons/`（側邊欄的在 `nav/`），不在這個資料夾，但來源和授權一起記在這裡。

- **來源**：[Lucide](https://lucide.dev)（https://github.com/lucide-icons/lucide）的 npm 套件 `lucide-static` 1.58.0，官方 tarball `lucide-static-1.58.0.tgz`（integrity `sha512-VFE2ZEq3yEwjBH0El9CA9lHgDWKKXPKgKxFzX+/JN1FPa4maQbtl/pT4B62aJkUEpODT5octDsyGy7tPVE2svQ==`，SHA-1 `838a9ef1c25275491cae326439c576ba0b930ef4`，和 npm registry 記錄的相同）。
  - 2026-11-01 新版介面（`docs/rebuild/new-UIUX/`）把 tarball 裡的 2159 個圖示放在本機的 `docs/rebuild/assets/sources/lucide-all/`，用 `docs/rebuild/assets/scripts/make_icon.py` 上色，兩個都被 git 忽略。
- **授權**：ISC。全文在 `icons/LICENSE-lucide.txt`，和 tarball 裡的 `LICENSE` 逐位元組相同。每個 SVG 都保留了原本第一行的 `<!-- @license lucide-static v1.58.0 - ISC -->`。
  - 下表標「Feather」的，是 Lucide `LICENSE` 列出的 Feather 衍生圖示（`circle-x`、`circle-alert`、`triangle-alert`、`circle-check` 列在舊名 `x-circle`、`alert-circle`、`alert-triangle`、`check-circle` 底下）。同一份授權檔裡 Feather 的 MIT 授權（Copyright (c) 2013-present Cole Bemis）也適用於它們。
- **修改**（ISC 允許）：只把 `stroke="currentColor"` 換成固定的顏色，其餘位元組和原檔相同。顏色在檔案裡預先上好，不在執行時染色（CONTRACTS §2：避免 layer 的成本）。
- **檔名**：`<Lucide 名稱>_<Theme token>.svg`，線條的顏色就是 `Theme.qml` 那個 token 的值。改了 `Theme.qml` 的顏色，要重新產生對應的圖示。
- **格式**：SVG，不轉成 PNG。Qt 的 SVG 外掛（`libqsvg`，PySide6 6.12 內建）畫得出每個檔案，沒有任何警告；`Image` 設了 `sourceSize` 時會依螢幕的 devicePixelRatio 重新點陣化，Retina 上也是清楚的。
- **測試**：`tests/unit/test_qml_components.py` 檢查每個檔案的顏色等於檔名寫的 token、畫得出來、沒有警告，而且每個檔案都有 QML 用到、用到的都存在。
- **側邊欄**：每個按鈕有兩份。一般是 `iconNeutral`（UD2：圖示預設中性灰），目前頁面是 `navItemCurrentText`（亮底 `#E6E8EB` 上的深色，15.5:1）。Log Out、Quit 不會是目前頁面，各只有一份。

| 檔案 | 顏色 | 用在 | 衍生自 |
|---|---|---|---|
| `arrow-down_errorOnDark.svg` | `errorOnDark` `#FF9A90` | `StatTile.qml` | Feather |
| `arrow-right_warningOnDark.svg` | `warningOnDark` `#FFB74D` | `OverviewCard.qml` | Feather |
| `arrow-up-from-line_iconNeutral.svg` | `iconNeutral` `#C3C8CF` | `record_widget.qml` |  |
| `arrow-up-right_iconNeutral.svg` | `iconNeutral` `#C3C8CF` | `server_home_button.qml` | Feather |
| `arrow-up_successOnDark.svg` | `successOnDark` `#81C784` | `StatTile.qml` | Feather |
| `audio-lines_iconNeutral.svg` | `iconNeutral` `#C3C8CF` | `OverviewCard.qml` |  |
| `calendar_iconNeutral.svg` | `iconNeutral` `#C3C8CF` | `DateField.qml`、`OverviewCard.qml` | Feather |
| `chevron-down_iconNeutral.svg` | `iconNeutral` `#C3C8CF` | `DeviceSelect.qml`、`HomeSelect.qml`、`admin_add_page.qml`、`admin_service_page.qml` | Feather |
| `chevron-down_textTitle.svg` | `textTitle` `#F3F4F6` | `ScoreTrendCard.qml` | Feather |
| `chevron-left_actionSecondaryText.svg` | `actionSecondaryText` `#ECEEF1` | `PageHeader.qml`、`ReportsView.qml` | Feather |
| `chevron-left_textDisabled.svg` | `textDisabled` `#6E747D` | `ReportsView.qml` | Feather |
| `chevron-right_actionSecondaryText.svg` | `actionSecondaryText` `#ECEEF1` | `ReportsView.qml` | Feather |
| `chevron-right_textDisabled.svg` | `textDisabled` `#6E747D` | `ReportsView.qml` | Feather |
| `circle-alert_errorOnDark.svg` | `errorOnDark` `#FF9A90` | `FieldError.qml`、`InlineError.qml`、`ServiceSearchField.qml`、`SettingsDevices.qml`、`Toast.qml`、`login.qml`、`record_widget.qml` | Feather |
| `circle-check_successOnDark.svg` | `successOnDark` `#81C784` | `SettingsDevices.qml`、`StatusChip.qml`、`Toast.qml`、`OverviewCard.qml`、`record_widget.qml` |  |
| `circle-dashed_textPrimary.svg` | `textPrimary` `#ECEEF1` | `StatusChip.qml` |  |
| `circle-user-round_iconNeutral.svg` | `iconNeutral` `#C3C8CF` | `PageHeader.qml` |  |
| `circle-x_errorOnDark.svg` | `errorOnDark` `#FF9A90` | `StatusChip.qml` | Feather |
| `clock_infoOnDark.svg` | `infoOnDark` `#90CAF9` | `StatusChip.qml`、`OverviewCard.qml`、`analysis.qml` | Feather |
| `clock_textSecondary.svg` | `textSecondary` `#B4BAC2` | `ServiceDetailView.qml` | Feather |
| `download_textSecondary.svg` | `textSecondary` `#B4BAC2` | `ServiceDetailView.qml` | Feather |
| `eye-off_actionSecondaryText.svg` | `actionSecondaryText` `#ECEEF1` | `admin_add_page.qml` |  |
| `eye_actionSecondaryText.svg` | `actionSecondaryText` `#ECEEF1` | `admin_add_page.qml` |  |
| `file-chart-column_chromeIcon.svg` | `chromeIcon` `#E6E8EB` | `ReportsView.qml` |  |
| `file-chart-column_textFaint.svg` | `textFaint` `#8E959F` | `ReportsView.qml` |  |
| `file-text_iconNeutral.svg` | `iconNeutral` `#C3C8CF` | `OverviewCard.qml` |  |
| `gauge_iconNeutral.svg` | `iconNeutral` `#C3C8CF` | `OverviewCard.qml` |  |
| `inbox_textDisabled.svg` | `textDisabled` `#6E747D` | `EmptyState.qml` |  |
| `info_infoOnDark.svg` | `infoOnDark` `#90CAF9` | `Toast.qml` | Feather |
| `loader-circle_actionPrimaryText.svg` | `actionPrimaryText` `#0E1013` | `BusyButton.qml` |  |
| `loader-circle_chromeOnLight.svg` | `chromeOnLight` `#0E1013` | `BusyButton.qml` |  |
| `loader-circle_textSecondary.svg` | `textSecondary` `#B4BAC2` | `BusyButton.qml`、`ServiceSearchField.qml`、`SettingsDevices.qml`、`record_widget.qml` |  |
| `mic-off_iconNeutral.svg` | `iconNeutral` `#C3C8CF` | `SettingsDevices.qml` |  |
| `mic_iconNeutral.svg` | `iconNeutral` `#C3C8CF` | `SettingsDevices.qml`、`record_widget.qml` |  |
| `pause_actionPrimaryText.svg` | `actionPrimaryText` `#0E1013` | `ServiceDetailView.qml` |  |
| `play_actionPrimaryText.svg` | `actionPrimaryText` `#0E1013` | `ServiceDetailView.qml` |  |
| `plus_actionPrimaryText.svg` | `actionPrimaryText` `#0E1013` | `ReportsView.qml`（New Report） |  |
| `rotate-cw_actionSecondaryText.svg` | `actionSecondaryText` `#ECEEF1` | `SettingsDevices.qml`、`record_widget.qml` |  |
| `scan-face_iconNeutral.svg` | `iconNeutral` `#C3C8CF` | `OverviewCard.qml` |  |
| `search_textFaint.svg` | `textFaint` `#8E959F` | `ServiceSearchField.qml` | Feather |
| `triangle-alert_iconNeutral.svg` | `iconNeutral` `#C3C8CF` | `OverviewCard.qml` | Feather |
| `triangle-alert_textOnDark.svg` | `textOnDark` `#FFFFFF` | `OverviewCard.qml` | Feather |
| `triangle-alert_warningOnDark.svg` | `warningOnDark` `#FFB74D` | `SettingsDevices.qml`、`Toast.qml`、`ServiceDetailView.qml`、`record_widget.qml` | Feather |
| `upload_chromeIcon.svg` | `chromeIcon` `#E6E8EB` | `Toast.qml` | Feather |
| `user-round-plus_actionPrimaryText.svg` | `actionPrimaryText` `#0E1013` | `admin_home.qml` |  |
| `video_actionPrimaryText.svg` | `actionPrimaryText` `#0E1013` | `OverviewCard.qml`、`record_widget.qml`、`server_home.qml` |  |
| `video-off_iconNeutral.svg` | `iconNeutral` `#C3C8CF` | `SettingsDevices.qml` |  |
| `video_iconNeutral.svg` | `iconNeutral` `#C3C8CF` | `SettingsDevices.qml`、`record_widget.qml` |  |
| `volume-2_iconNeutral.svg` | `iconNeutral` `#C3C8CF` | `ServiceDetailView.qml` |  |
| `volume-x_iconNeutral.svg` | `iconNeutral` `#C3C8CF` | `ServiceDetailView.qml` |  |
| `x_chromeIcon.svg` | `chromeIcon` `#E6E8EB` | `Toast.qml` | Feather |
| `nav/brain-circuit_iconNeutral.svg` | `iconNeutral` `#C3C8CF` | 側邊欄（`SidebarButton.qml`） |  |
| `nav/brain-circuit_navItemCurrentText.svg` | `navItemCurrentText` `#0E1013` | 側邊欄（`SidebarButton.qml`） |  |
| `nav/file-chart-column_iconNeutral.svg` | `iconNeutral` `#C3C8CF` | 側邊欄（`SidebarButton.qml`） |  |
| `nav/file-chart-column_navItemCurrentText.svg` | `navItemCurrentText` `#0E1013` | 側邊欄（`SidebarButton.qml`） |  |
| `nav/history_iconNeutral.svg` | `iconNeutral` `#C3C8CF` | 側邊欄（`SidebarButton.qml`） |  |
| `nav/history_navItemCurrentText.svg` | `navItemCurrentText` `#0E1013` | 側邊欄（`SidebarButton.qml`） |  |
| `nav/house_iconNeutral.svg` | `iconNeutral` `#C3C8CF` | 側邊欄（`SidebarButton.qml`） |  |
| `nav/house_navItemCurrentText.svg` | `navItemCurrentText` `#0E1013` | 側邊欄（`SidebarButton.qml`） |  |
| `nav/log-out_iconNeutral.svg` | `iconNeutral` `#C3C8CF` | 側邊欄（`SidebarButton.qml`）：Log Out | Feather |
| `nav/power_iconNeutral.svg` | `iconNeutral` `#C3C8CF` | 側邊欄（`SidebarButton.qml`）：Quit | Feather |
| `nav/settings_iconNeutral.svg` | `iconNeutral` `#C3C8CF` | 側邊欄（`SidebarButton.qml`） |  |
| `nav/settings_navItemCurrentText.svg` | `navItemCurrentText` `#0E1013` | 側邊欄（`SidebarButton.qml`） |  |
| `nav/user-round-plus_iconNeutral.svg` | `iconNeutral` `#C3C8CF` | 側邊欄（`SidebarButton.qml`） |  |
| `nav/user-round-plus_navItemCurrentText.svg` | `navItemCurrentText` `#0E1013` | 側邊欄（`SidebarButton.qml`） |  |
| `nav/video_iconNeutral.svg` | `iconNeutral` `#C3C8CF` | 側邊欄（`SidebarButton.qml`） |  |
| `nav/video_navItemCurrentText.svg` | `navItemCurrentText` `#0E1013` | 側邊欄（`SidebarButton.qml`） |  |

來源 SVG 的 SHA-256（上色之前；把顏色換回 `currentColor`，每個檔案都和這裡的相同）：

```text
66bf6bb1814f35e1ac2fee13dcf0de5e41632886844ecdc7340b9a7664ab899b  arrow-down.svg
dea34a61ed7692d93320288aedc7e3cc74eff4dae5d984d2369843899149d3d3  arrow-right.svg
8dfcc45a80400884e460fa2538b67760a71c2601a279fe48ba53dee02b5265f0  arrow-up.svg
3a9b8037387df9d552042e229aa9a16ae7326b52bf2a5aab07450f3b539a404c  arrow-up-from-line.svg
482be587f746204a2d3d2f5a8b50a9815c5163563306f66eba0960421535066d  arrow-up-right.svg
44068bd04fe9fe93457923d35659e5aa98de5d1235721fb7827458188b9a29dd  audio-lines.svg
e6dad1cc4203645e6ac333346a2efb47813fb7dd198add19cb0e886a56a31547  brain-circuit.svg
ee7878329aa842b6f203393e6daf3e56ffc33bab4d23d4ad6c5a931d5ed96eac  calendar.svg
9c19fc2ed7b1e409efe9be784be57d71add9a23114cc06ec38b6947abf4aa49d  chevron-down.svg
463c58e473ce3179696d051b37f22f13ee917227526c04a994cb794bcbd2ab15  chevron-left.svg
f2f751f120735e513fbd1f89fa9a5785832eda8dc301c442fd918582641c3f9a  chevron-right.svg
136cb1a270216ccd678be230acbab73771349372b8ecb9283116528d5c4ae269  circle-alert.svg
e963fb03e3180118ca0b99fba7cc9644769dc61a35280e3da75fc3a65fd450c4  circle-check.svg
2ea5a8a81920485da3eb70bdc8e2c349df0311fd7888ba0e9d85dcac0db3b512  circle-dashed.svg
c7e6ee719aeb7c0e71d6afefa3f8946fcd7232506e881d9666a86f8904ba23bd  circle-user-round.svg
2b16f7f66feba8412175c1b3c45f793cb603f803b6c9de6daa16a7a928850cbd  circle-x.svg
863f184bbb4460fd133fb3da9b84b9158017d6af5b187c9f09b79cae0d1f972d  clock.svg
195448b5abcebcebbb2931d3b4d3079fbe26590f8b197033158c4cc4c9100a48  download.svg
602bee368f3255e925846b7d5918f5621f3149a8635264444e87a04a4e77599d  eye.svg
8ad0e0da4c96966e89e67aab7ee1b43fd39ce1dcc4ecadfff3c6d0e247dff50f  eye-off.svg
299cfa41fd9008e0c55af2b35d9738894eafa50e2053411a1ad40b57f52990e5  file-chart-column.svg
aa15a4da3a69c0757b771c4398ff10308121e89e2cdd571496c19374dc2f9f8a  file-text.svg
3aaef15cccfa8f9d2e60df8e374f35bb5b64530bf27075e3aeff07bb4d244086  gauge.svg
c40d5f3133df70f9eaf3fdf03234c74995b10b2cc330e2d3b2ad73aeb0cf4409  history.svg
dbfcfe7ae82b8d9d092c2165d21aa6b93330f8b4c04e6d5e1b270b905f13df9a  house.svg
7d54bce88627817f1eafb17b4bb79ea2ee444c6a661b843afc49e731b859ccd8  inbox.svg
bd039cbc60abc0af2d241232fa6fe76da45c974a8928141b266b15435ca26e0c  info.svg
8ef4c230a13382644c11c1042e120c9cc00196856765bf84bde2362d671d0b50  loader-circle.svg
595115f16ef08ee211d577e0dab39e448faf9ba85b67ac21704e01656c7724ce  log-out.svg
b22e81c1ed6523da0a711af1fd25086b5bea96dd8f3fba7aa87fc42b025517d5  mic.svg
4feda43e74b4dc00d72e65cfae9caf534c6ed1bd9f09c1a49088826006bec3e0  mic-off.svg
d764efc7d23340a615d7cec56ca0a6e7fb10ba2474559a2267535d21b66e0672  pause.svg
99eaf96106efd57b9ac5abfe436918ac3bf57dca430c5bb1b0941b204ae87141  play.svg
8abf820bdff94541a7909db589153e96786c283ad3cdda80b815413da7900a43  plus.svg
dd6ff4af3d5e0a6d2ae6167f62b2edf3b45389ff36abec1a1a5980d53f57a102  power.svg
7f52a2ff0ceae30cb075fe04ecec64a83eee08b5bf8381ebe2e189126b826485  rotate-cw.svg
78ad58a828761b1f4a1fbe68acf4a9f4cbc89b2dc66e671888f1038a21498a3f  scan-face.svg
014a4a94c906b6a363be5c070d22a3323eff9b40e4caca52b97e0c098b690a85  search.svg
109df42e03bb5382fc66f3195e30463cc8ff6f2edd8d8597e62c2de862bd0140  settings.svg
57b21e3ead643bb1eb69e0eed7fbcd8820b77491124c26b1bb342b0e1079711a  triangle-alert.svg
868173535cd3a65071d2527e5284ad1d1a3144464f8a107fdab47314c2a945e8  upload.svg
fc3d8f3566fbce5eeb689eff876627c3c8dc242ff2e5516e3dc0783c3bb56a60  user-round-plus.svg
898a1b8b815739f79aa95031428c2c11a35dd4b0492029ae407a8881250d489a  video.svg
0a209e33d128ce1a67d65857525cd73dc89ad8c2b2cdffcbf11355acdec335bb  video-off.svg
9b629a707fe10ea4f8da68359b3a6ca63cf48ce4e97bfcdb5820cbd70e3c1bf1  volume-2.svg
a18b12a652ddfa64c87bccafe0425f1065eb20d56fe9709e97593e87c0eaa740  volume-x.svg
4ab0e1e5031230da80a2ddaf9c8639bde234a974a1bc94b7dfbac7af14f55a25  x.svg
```
