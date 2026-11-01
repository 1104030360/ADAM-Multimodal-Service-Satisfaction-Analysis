pragma Singleton
import QtQuick

// 設計系統的 token：03_design_system.md 的 DS-10～DS-16 和 DS-40。整個 app 只有這一份（DS-04）。
// 頁面和元件的顏色、時間值一律從這裡取，不寫死。屬性名稱就是 DS 表格的「QML」欄；標「新增」的是 DS 沒有命名、元件需要的值。
// 外殼用 Binding 把 uiPrefs 寫進 motionMode、animationScale（ENG-18）；元件測試把 animationScale 設成 0。
QtObject {
    // ── DS-10 深色介面的中性色（登入、側邊欄、toast）
    readonly property color chromeWindow: "#0B0C0E"
    readonly property color chromePanel: "#0E1013"
    readonly property real chromePanelOpacity: 0.78
    readonly property color chromeField: "#17191D"
    readonly property color chromeBorder: "#596069"
    readonly property color chromeFieldBorder: "#606770"
    readonly property color chromeFocus: "#A9B0BA"
    readonly property color chromeSelection: "#4B515A"      // 新增：登入頁輸入框選取文字的底色（白字 8.0:1）
    readonly property color chromeTextMuted: "#8E959F"
    readonly property color chromeText: "#ECEEF1"
    readonly property color chromeTitle: "#F3F4F6"
    readonly property color chromeIcon: "#E6E8EB"
    readonly property color chromeButtonHover: "#FFFFFF"
    readonly property color chromeButtonPressed: "#C3C8CF"
    readonly property color chromeOnLight: "#0E1013"
    readonly property color sidebarBg: "#333333"
    readonly property color navItem: "#444444"
    readonly property color navItemHover: "#555555"
    readonly property color navItemPressed: "#666666"
    readonly property color navItemCurrent: "#E6E8EB"
    readonly property color navItemCurrentText: "#0E1013"

    // ── DS-11 淺色內容頁的中性色
    readonly property color contentBg: "#FFFFFF"
    readonly property color surfaceAlt: "#F2F6FA"
    readonly property color surfaceMuted: "#EFEFEF"
    readonly property color track: "#E0E0E0"
    readonly property color divider: "#D1D1D1"
    readonly property color textPrimary: "#333333"
    readonly property color textSecondary: "#596069"
    readonly property color textDisabled: "#8E959F"

    // ── DS-12 動作色
    readonly property color actionPrimary: "#333333"
    readonly property color actionPrimaryHover: "#444444"
    readonly property color actionPrimaryPressed: "#555555"
    readonly property color actionSecondaryBorder: "#596069"
    readonly property color actionSecondaryBg: "#FFFFFF"
    readonly property color actionSecondaryText: "#333333"
    readonly property color actionDanger: "#C62828"
    readonly property color actionDisabledBg: "#E0E0E0"
    readonly property color actionDisabledText: "#8E959F"
    readonly property color segmentSelected: "#333333"
    readonly property color segmentSelectedText: "#FFFFFF"
    readonly property color segmentIdle: "#E0E0E0"
    readonly property color segmentIdleText: "#333333"
    readonly property color textOnDark: "#FFFFFF"           // 新增：主要按鈕的字、toast 標題

    // ── DS-13 語意色：淺色上的文字與圖示、淺色色塊、深色上（toast）
    readonly property color successOnLight: "#2E7D32"
    readonly property color successTint: "#E8F5E9"
    readonly property color successOnDark: "#81C784"
    readonly property color infoOnLight: "#1565C0"
    readonly property color infoTint: "#E3F2FD"
    readonly property color infoOnDark: "#90CAF9"
    readonly property color warningOnLight: "#9C5100"
    readonly property color warningTint: "#FFF3E0"
    readonly property color warningOnDark: "#FFB74D"
    readonly property color errorOnLight: "#C62828"
    readonly property color errorTint: "#FFEBEE"
    readonly property color errorOnDark: "#FF9A90"
    readonly property color recording: "#E53935"
    readonly property color focusOnLight: "#1565C0"
    readonly property color focusOnDark: "#E6E8EB"

    // ── DS-14 字級（px）。字標以外一律用系統字型
    readonly property int fontDisplay: 60
    readonly property int wordmarkSpacing: 16
    readonly property int fontHeadline: 24
    readonly property int fontTitle: 22
    readonly property int fontTitleSmall: 20
    readonly property int fontBodyLarge: 18
    readonly property int fontBody: 16
    readonly property int fontCaption: 14

    // ── DS-15 間距、圓角、尺寸
    readonly property int spaceXS: 5
    readonly property int spaceS: 10
    readonly property int spaceM: 16
    readonly property int spaceL: 20
    readonly property int spaceXL: 24
    readonly property int space2XL: 30
    readonly property int space3XL: 40
    readonly property int space4XL: 50
    readonly property int radiusS: 5
    readonly property int radiusM: 10
    readonly property int radiusL: 20
    readonly property int controlHeight: 40
    readonly property int controlHeightL: 50
    readonly property int chipHeight: 24
    readonly property int iconS: 16
    readonly property int iconM: 20
    readonly property int iconNav: 30
    readonly property int toastWidth: 360
    readonly property int toastWidthMin: 320
    readonly property int toastWidthMax: 400
    readonly property int hitMin: 32

    // ── DS-16 層次與疊放順序
    readonly property int zContent: 0
    readonly property int zPageOverlay: 10
    readonly property int zDialog: 100
    readonly property int zToast: 200
    readonly property color scrim: Qt.rgba(0, 0, 0, 0.4)      // 新增：對話框背後變暗

    // ── 元件用的別名（DS-21～DS-24），值都取自上面的 token
    readonly property color toastBg: sidebarBg
    readonly property color toastBorder: chromeBorder
    readonly property color toastTitle: textOnDark
    readonly property color toastText: chromeIcon
    readonly property color toastButtonHover: navItem
    readonly property color toastButtonPressed: navItemHover
    readonly property color toastTrack: navItemHover           // toast 裡的進度條：軌道 #555555、填色 #E6E8EB
    readonly property color toastFill: chromeIcon
    readonly property color progressFill: textPrimary          // 淺色底的進度條：軌道 track、填色 #333333

    // ── DS-40 動態。reduceMotion 為 true 時所有 dur* 都是 0；否則乘上 animationScale
    property int motionMode: 0              // 0 跟隨系統、1 減少、2 完整（設定頁，ENG-20）
    property real animationScale: 1        // 測試設成 0
    readonly property bool systemReduceMotion:
        Application.styleHints.accessibility.motionPreference === Qt.MotionPreference.ReducedMotion
    readonly property bool reduceMotion: motionMode === 1 || (motionMode === 0 && systemReduceMotion)
    readonly property real motionScale: reduceMotion ? 0 : Math.max(0, animationScale)
    readonly property int durFast: Math.round(100 * motionScale)
    readonly property int durBase: Math.round(150 * motionScale)
    readonly property int durEnter: Math.round(200 * motionScale)
    readonly property int durSplash: Math.round(400 * motionScale)
    readonly property int durSpin: Math.round(1000 * motionScale)          // 新增：M-7 旋轉圖示一圈
    readonly property int durIndeterminate: Math.round(1500 * motionScale) // 新增：M-8 不定進度條跑一趟
    // 門檻不是動畫：減少動態、animationScale 都不影響（DS-24、DS-25）
    readonly property int busyDelay: 300
    readonly property int busyMinShow: 500
    readonly property int slowAfter: 2000
    readonly property int easeEnter: Easing.OutCubic
    readonly property int easeExit: Easing.InCubic
    readonly property int easeMove: Easing.InOutCubic

    // DS-23 的數字格式：「58.6 / 124.0 MB · 47%」。done、total 是 bytes；total 不大於 0（不知道總量）時回傳空字串。
    // MB 取一位小數、百分比取整數（無條件捨去，還沒傳完不會顯示 100%）。
    function transferText(done, total) {
        if (!(total > 0))
            return ""
        const finished = Math.max(0, Math.min(done, total))
        return qsTr("%1 / %2 MB · %3%")
            .arg((finished / 1e6).toFixed(1))
            .arg((total / 1e6).toFixed(1))
            .arg(Math.floor(finished / total * 100))
    }
}
