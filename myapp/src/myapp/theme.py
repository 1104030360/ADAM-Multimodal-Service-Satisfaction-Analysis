"""QtWidgets（錄影視窗）用的顏色：和 qml/components/Theme.qml 同名、同值（ENG-18 坑 5）。

QtWidgets 讀不到 QML 的 Theme 單例，所以 QSS 用到的幾個值放在這裡。單一來源是 Theme.qml：
tests/unit/test_ui_copy.py 解析 Theme.qml，逐一比對 TOKENS；改了 Theme.qml 卻忘了改這裡，測試會失敗。
不在執行時解析 Theme.qml：打包後的路徑、別名（toastBg: sidebarBg）都要另外處理，為了十幾個值不值得。
"""

# Theme.qml 的 token（名稱和 Theme.qml 一樣，DS-11～DS-13）
TOKENS = {
    "contentBg": "#FFFFFF",
    "surfaceAlt": "#F2F6FA",
    "surfaceMuted": "#EFEFEF",
    "track": "#E0E0E0",
    "textPrimary": "#333333",
    "textSecondary": "#596069",
    "textOnDark": "#FFFFFF",
    "progressFill": "#333333",
    "actionPrimary": "#333333",           # 主要按鈕是石墨灰，白字 12.63:1（D2、DS-12）
    "actionPrimaryHover": "#444444",
    "actionPrimaryPressed": "#555555",
    "actionSecondaryBorder": "#596069",
    "actionDisabledBg": "#E0E0E0",
    "actionDisabledText": "#8E959F",
    "successOnLight": "#2E7D32",
    "errorOnLight": "#C62828",
    "recording": "#E53935",               # 只用在實心的錄影圓點，不用在按鈕和文字（DS-13）
    "focusOnLight": "#1565C0",
}

# 只有錄影視窗用、Theme.qml 沒有的值：視窗底色和鏡頭預覽框（DS-34；設計稿「錄影與上傳」沿用以前的值）
RECORD_WINDOW = {
    "windowBg": "#F0F0F0",
    "previewBg": "#D3D3D3",
    "previewBorder": "#B0B0B0",
}
