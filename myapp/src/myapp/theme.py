"""QtWidgets（錄影視窗）用的顏色：和 qml/components/Theme.qml 同名、同值（ENG-18 坑 5）。

QtWidgets 讀不到 QML 的 Theme 單例，所以 QSS 用到的幾個值放在這裡。單一來源是 Theme.qml：
tests/unit/test_ui_copy.py 解析 Theme.qml，逐一比對 TOKENS；改了 Theme.qml 卻忘了改這裡，測試會失敗。
不在執行時解析 Theme.qml：打包後的路徑、別名（toastBg: sidebarBg）都要另外處理，為了十幾個值不值得。
"""

# Theme.qml 的 token（名稱和 Theme.qml 一樣，DS-11～DS-13）。新版介面改成深色（new-UIUX PLAN 第 3 節）
TOKENS = {
    "card": "#15171B",
    "cardBorder": "#24272C",              # 錄影視窗：下面那一條、預覽框的框線
    "track": "#2C3036",
    "textTitle": "#F3F4F6",               # 錄影視窗：狀態、經過時間、預覽的標題
    "textMuted": "#9AA1AB",
    "textFaint": "#8E959F",               # 錄影視窗：預覽標題旁的裝置名稱
    "iconNeutral": "#C3C8CF",             # 錄影視窗：一般的圖示（麥克風、上傳）
    "textOnDark": "#FFFFFF",
    "progressFill": "#E6E8EB",
    "actionPrimary": "#E6E8EB",           # 主要按鈕是淺色膠囊，深色字 15.5:1（UD3）
    "actionPrimaryHover": "#FFFFFF",
    "actionPrimaryPressed": "#C3C8CF",
    "actionPrimaryText": "#0E1013",
    "actionSecondaryBg": "#1C1F24",
    "actionSecondaryHover": "#24282E",
    "actionSecondaryPressed": "#2C3036",
    "actionSecondaryBorder": "#3A3F46",
    "actionSecondaryText": "#ECEEF1",
    "actionDanger": "#C62828",            # 錄影視窗的 Stop Recording：紅色實心，白字 5.6:1（設計稿 StaffRecorder）
    "actionDisabledBg": "#1A1D21",
    "actionDisabledText": "#6E747D",
    "successOnDark": "#81C784",
    "errorOnDark": "#FF9A90",
    "recording": "#E53935",               # 只用在實心的錄影圓點，不用在按鈕和文字（DS-13）
    "focusOnDark": "#E6E8EB",
}

# 只有錄影視窗用、Theme.qml 沒有的值（設計稿 StaffRecorder）
RECORD_WINDOW = {
    "windowBg": "#0B0C0E",
    "previewBg": "#000000",               # 鏡頭預覽的底：畫面沒填滿時是黑邊
    "stopHover": "#D32F2F",               # Stop Recording 的 hover、按下：白字 5.0:1、6.6:1
    "stopPressed": "#B71C1C",
}
