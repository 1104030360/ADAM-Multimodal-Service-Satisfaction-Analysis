pragma Singleton
import QtQuick

// 設計系統的 token：03_design_system.md 的 DS-10～DS-16 和 DS-40。整個 app 只有這一份（DS-04）。
// 頁面和元件的顏色、時間值一律從這裡取，不寫死。屬性名稱就是 DS 表格的「QML」欄；標「新增」的是 DS 沒有命名、元件需要的值。
// 新版介面（docs/rebuild/new-UIUX/PLAN.md 第 3 節）把整個 app 改成深色（UD1），沒有淺色模式：內容、側邊欄、卡片都是深底淺字。
// 外殼用 Binding 把 uiPrefs 寫進 motionMode、animationScale（ENG-18）；元件測試把 animationScale 設成 0。
QtObject {
    // ── DS-10 登入頁的深色面板與輸入框（login.qml）
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

    // ── 側邊欄（PLAN 第 4 節）：寬 96，圖示方塊 44，字在圖示下面；目前頁面是亮底深色圖示
    readonly property color railBg: "#111316"              // 新增
    readonly property color railBorder: "#1F2226"          // 新增：側邊欄右邊的線
    readonly property color navItem: "#1A1D21"             // 圖示方塊
    readonly property color navItemBorder: "#262A30"       // 新增
    readonly property color navItemHover: "#22262B"
    readonly property color navItemPressed: "#2A2E34"
    readonly property color navItemCurrent: "#E6E8EB"      // 和主要按鈕同一組顏色
    readonly property color navItemCurrentText: "#0E1013"

    // ── DS-11 內容的中性色（PLAN 第 3 節）：深色的底、卡片、方塊，淺色的字。對比是在卡片 #15171B 上算的
    readonly property color contentBg: "#0B0C0E"           // 視窗、內容區的底色；等於 chromeWindow
    readonly property color card: "#15171B"                // 新增：卡片、對話框
    readonly property color cardBorder: "#24272C"          // 新增：卡片的框線、分隔線、表格的底線
    readonly property color tile: "#1A1D21"                // 新增：圖示方塊、骨架、選到的清單項目
    readonly property color tileBorder: "#2C3036"          // 新增：圖示方塊的框線
    readonly property color tileSelected: "#1F2328"        // 新增：選到的選項方塊、清單項目（設定頁、報表清單）
    readonly property color field: "#17191D"               // 新增：輸入框、下拉選單的底；等於 chromeField
    readonly property color fieldBorder: "#606770"         // 新增：輸入框的框線，在卡片上 3.1:1（WCAG 1.4.11）
    readonly property color track: "#2C3036"               // 進度條、滑桿的軌道
    readonly property color textTitle: "#F3F4F6"           // 新增：標題、大數字
    readonly property color textPrimary: "#ECEEF1"         // 內文，15.4:1
    readonly property color textSecondary: "#B4BAC2"       // 方塊的標籤，9.3:1
    readonly property color textMuted: "#9AA1AB"           // 新增：副標、說明，7.0:1
    readonly property color textFaint: "#8E959F"           // 新增：最小的字（11～12 px），6.0:1；側邊欄上 6.2:1
    readonly property color textDisabled: "#6E747D"        // 停用的字、裝飾用的圖示（WCAG 不要求對比）
    readonly property color iconNeutral: "#C3C8CF"         // 新增：一般的圖示（UD2：圖示預設中性灰，顏色留給狀態）

    // ── DS-12 動作色：主要按鈕是淺色膠囊（UD3），和登入按鈕、目前頁面同一組
    readonly property color actionPrimary: "#E6E8EB"
    readonly property color actionPrimaryHover: "#FFFFFF"
    readonly property color actionPrimaryPressed: "#C3C8CF"
    readonly property color actionPrimaryText: "#0E1013"   // 新增：15.5:1
    readonly property color actionSecondaryBg: "#1C1F24"
    readonly property color actionSecondaryHover: "#24282E"     // 新增
    readonly property color actionSecondaryPressed: "#2C3036"   // 新增
    readonly property color actionSecondaryBorder: "#3A3F46"
    readonly property color actionSecondaryText: "#ECEEF1"
    readonly property color actionDanger: "#C62828"        // 危險動作的實心底，白字 5.6:1
    readonly property color actionDisabledBg: "#1A1D21"
    readonly property color actionDisabledText: "#6E747D"
    readonly property color segmentBg: "#0F1114"           // 新增：分段切換的外框底
    readonly property color segmentSelected: "#E6E8EB"
    readonly property color segmentSelectedText: "#0E1013"
    readonly property color segmentIdle: "#0F1114"
    readonly property color segmentIdleText: "#C3C8CF"     // 在 #0F1114 上 11.5:1
    readonly property color textOnDark: "#FFFFFF"          // 新增：飽和填色（分數長條、危險按鈕）上的白字、toast 標題

    // ── DS-13 語意色。深色上的字和圖示用 *OnDark（在卡片上都超過 9:1）；*OnLight 是飽和的填色
    // （分數長條、待辦的方塊，上面放白字，和卡片 3.1:1 以上），也是淺色「紙」上的字；*Tint 是深色的淡底
    readonly property color successOnLight: "#2E7D32"
    readonly property color successTint: "#1A291F"
    readonly property color successOnDark: "#81C784"
    readonly property color infoOnLight: "#1565C0"
    readonly property color infoTint: "#152539"
    readonly property color infoOnDark: "#90CAF9"
    readonly property color warningOnLight: "#9C5100"
    readonly property color warningTint: "#2D2116"
    readonly property color warningOnDark: "#FFB74D"
    readonly property color errorOnLight: "#C62828"
    readonly property color errorTint: "#351A1D"
    readonly property color errorOnDark: "#FF9A90"
    readonly property color recording: "#E53935"
    readonly property color focusOnDark: "#E6E8EB"         // 鍵盤焦點框，全部的頁面都用這個
    readonly property color paper: "#E6E8EB"               // 新增：白底的圖表、PDF 頁面墊的淺灰「紙」（UD8）

    // ── 圖表（PLAN 第 3、5 節）
    readonly property color compareBar: "#60666F"          // 新增：長條圖裡用來比較的灰色長條，和卡片 3.1:1
    readonly property color guide: "#3A3F46"               // 新增：圖表的虛線（85、70）
    readonly property color trendPanel: "#17305C"          // 新增：趨勢圖的藍色卡片（UD4）
    readonly property color trendPanelBorder: "#24467F"
    readonly property color trendPanelText: "#A9C1E8"      // 在藍底上 7.1:1
    readonly property color trendGuide: "#3F5F96"
    readonly property color trendControl: "#1F3C70"
    readonly property color trendControlBorder: "#36588F"
    readonly property color trendCustomerLine: "#FFFFFF"
    readonly property color trendStaffLine: "#8FB8F5"      // 在藍底上 6.4:1

    // ── DS-14 字級（px）。字標以外一律用系統字型
    readonly property int fontDisplay: 60
    readonly property int wordmarkSpacing: 16
    readonly property int fontPageTitle: 26                // 新增：頁首的頁面標題
    readonly property int fontHeadline: 24
    readonly property int fontTitle: 22
    readonly property int fontTitleSmall: 20               // 卡片標題
    readonly property int fontBodyLarge: 18
    readonly property int fontBody: 16
    readonly property int fontInput: 15                    // 新增：搜尋框、輸入框
    readonly property int fontCaption: 14
    readonly property int fontSmall: 13                    // 新增：分數帶、變化
    readonly property int fontLabel: 12                    // 新增：區塊的小標題（大寫）、圖表的刻度
    readonly property int fontNavLabel: 11                 // 新增：側邊欄圖示下面的字
    readonly property int fontStat: 36                     // 新增：數字方塊的大數字（等寬數字）

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
    readonly property int radiusNav: 12                    // 新增：側邊欄的圖示方塊
    readonly property int radiusTile: 14                   // 新增：數字方塊的圖示方塊
    readonly property int radiusCard: 16                   // 新增：卡片
    readonly property int radiusPill: 22                   // 新增：膠囊按鈕、搜尋框（高 44 的一半）
    readonly property int controlHeight: 44                // 按鈕、可以點的方塊至少 44（PLAN 第 2 節）
    readonly property int controlHeightL: 50
    readonly property int chipHeight: 24
    readonly property int iconS: 16
    readonly property int iconM: 20
    readonly property int iconNav: 22                      // 側邊欄方塊裡的圖示
    readonly property int navTile: 44                      // 新增：側邊欄的圖示方塊
    readonly property int statTile: 52                     // 新增：數字方塊的圖示方塊
    readonly property int railWidth: 96                    // 新增：側邊欄的寬度
    readonly property int pagePaddingH: 32                 // 新增：內容區左右的留白
    readonly property int pagePaddingTop: 24               // 新增
    readonly property int pagePaddingBottom: 32            // 新增
    readonly property int contentMaxWidth: 1360            // 新增：內容區最寬，螢幕更寬時置中
    readonly property int cardPaddingV: 24                 // 新增：卡片上下的內距
    readonly property int cardPaddingH: 28                 // 新增：卡片左右的內距
    readonly property int sectionGap: 24                   // 新增：頁首、卡片之間的間距
    readonly property int tableRow: 60                     // 新增：表格一列（按鈕 44 加上下 8，PLAN 6-1）
    readonly property int toastWidth: 360
    readonly property int toastWidthMin: 320
    readonly property int toastWidthMax: 400
    readonly property int hitMin: 32

    // ── DS-16 層次與疊放順序
    readonly property int zContent: 0
    readonly property int zPageOverlay: 10
    readonly property int zDialog: 100
    readonly property int zToast: 200
    readonly property color scrim: Qt.rgba(0, 0, 0, 0.6)      // 新增：對話框背後變暗（深色底上要更暗才分得出來）

    // ── 元件用的別名（DS-21～DS-24），值都取自上面的 token
    readonly property color toastBg: "#333333"                 // toast 比內容的深色亮一階，浮在頁面上
    readonly property color toastBorder: chromeBorder
    readonly property color toastTitle: textOnDark
    readonly property color toastText: chromeIcon
    readonly property color toastButtonHover: "#444444"
    readonly property color toastButtonPressed: "#555555"
    readonly property color toastTrack: toastButtonPressed     // toast 裡的進度條：軌道 #555555、填色 #E6E8EB
    readonly property color toastFill: chromeIcon
    readonly property color progressFill: actionPrimary        // 深色底的進度條：軌道 track、填色 #E6E8EB

    // ── 分數帶（PLAN 第 2 節）：Good 85 以上、Fair 70～84、Needs improvement 70 以下。
    // 分數旁邊一定寫字（bandName），顏色不是唯一的線索。-1、null 是「沒有資料」：回傳空字串和中性色
    function hasScore(score) {
        return score !== null && score !== undefined && !isNaN(score) && score >= 0
    }

    function bandName(score) {
        if (!hasScore(score))
            return ""
        return score >= 85 ? qsTr("Good") : score >= 70 ? qsTr("Fair") : qsTr("Needs improvement")
    }

    function bandColor(score) {     // 深色底上的字、箭頭
        if (!hasScore(score))
            return textMuted
        return score >= 85 ? successOnDark : score >= 70 ? warningOnDark : errorOnDark
    }

    function bandFill(score) {      // 長條、方塊的填色，上面是白字
        if (!hasScore(score))
            return compareBar
        return score >= 85 ? successOnLight : score >= 70 ? warningOnLight : errorOnLight
    }

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
