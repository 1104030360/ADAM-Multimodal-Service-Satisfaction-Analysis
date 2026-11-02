pragma ComponentBehavior: Bound
import QtQuick
// 自訂了 background，要用 Basic 樣式（T13）。在這個檔案裡 import，用它的頁面不必換掉自己的樣式。
// MonthGrid、DayOfWeekRow 從 Qt 6.3 起在 QtQuick.Controls 裡（Basic 也有）
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "../components"

// 日期欄：兩端的 Services 頁共用（UX-46），長得一樣、錯誤的樣子也一樣（設計稿 StaffServices、AdminServices）。
// - 高 44、圓角 12，左邊是月曆圖示，placeholder「YYYY-MM-DD」，等寬數字。框線是 fieldBorder（和卡片 3.1:1），
//   取得焦點時 2 px 的 focusOnDark，有錯（invalid）時 2 px 的 errorOnDark（DS-27）。
// - 錯誤的文字由頁面放在欄位下方的 FieldError；這裡把它寫進 Accessible.description（errorText）。
// - 欄位上面的標籤（From、To）由頁面放；placeholder 不能當標籤用，頁面要給 accessibleName（DS-31）。
//
// 點選的月曆（new-feature N2）。對外照舊只有 text（YYYY-MM-DD，空的是不限）和 invalid，頁面和查詢都不用改：
// - 點一下欄位：打開月曆，焦點留在欄位上，照樣可以打字；打出完整的日期時月曆跟著跳到那一天。
//   點月曆圖示、或在欄位按 ↓：打開月曆，焦點進到日期上。
// - 月曆：上面是上一個月、Month、Year、下一個月，下面是 Qt 的 DayOfWeekRow、MonthGrid。一週從星期日開始（en_US），
//   「今天」是這台電腦的日期（today 屬性給測試固定日期），日期下面有一個點、字比較粗；選到的那天是亮底。
// - 左邊的快捷：Today、Clear；rangeOptions 打開時多 Last 7 days、This month。
// - 月曆疊在 popupZ 這一層：放在頁面上是 zPageOverlay；放在對話框裡（報表的 New Report）要設 Theme.zDialogOverlay，
//   不然月曆在對話框後面、點不到。不一律設高，是因為頁面上月曆開著時跳出的確認（⌘Q 的 Quit）要蓋在月曆上面。
// - 選了一天（或 Today、Clear）：改 text、關掉月曆、焦點回到欄位，並發出 textEdited()（和打字一樣，頁面照舊清掉錯誤）。
//   Last 7 days、This month 是一段期間，一個欄位裝不下：發出 rangeChosen(start, end)，由頁面把兩格都填好（T35）。
//   選了不會自己查詢，要按 Search。結束日早於開始日的錯誤照舊在按 Search 時檢查（service_list.date_errors）。
// - 鍵盤（焦點在日期上）：←→ 前後一天、↑↓ 前後一週、Page Up／Page Down 前後一個月、Home／End 這個月的第一天、
//   最後一天；Enter、Return、Space 選定；Esc 關掉；Tab 在月曆裡的控制項之間移動。關掉之後焦點回到欄位。
TextField {
    id: field

    property bool invalid: false
    property string accessibleName: ""
    property string errorText: ""
    property bool rangeOptions: false   // 快捷多 Last 7 days、This month（選了發 rangeChosen）
    property int popupZ: Theme.zPageOverlay   // 月曆疊在哪一層；在對話框裡用 Theme.zDialogOverlay
    property string today: ""           // 「今天」（YYYY-MM-DD）；空的是這台電腦的日期。測試用
    property var announced: []          // 鍵盤移動時唸過的日期（Accessible.announce 在測試裡看不到，T17 的做法）
    readonly property bool calendarOpen: calendar.visible
    readonly property var cursorDate: internal.cursor     // 鍵盤停在哪一天（Date；還沒打開過是 null）
    readonly property var calendarLocale: Qt.locale("en_US")   // 介面是英文；en_US 的一週從星期日開始

    signal rangeChosen(string start, string end)

    implicitWidth: 220
    implicitHeight: Theme.controlHeight
    leftPadding: 42             // 月曆圖示 18 + 左右的空白
    rightPadding: 14
    font.pixelSize: Theme.fontInput
    font.features: { "tnum": 1 }
    color: Theme.textPrimary
    placeholderText: qsTr("YYYY-MM-DD")
    placeholderTextColor: Theme.textFaint
    selectionColor: Theme.chromeSelection
    selectedTextColor: Theme.textTitle
    inputMethodHints: Qt.ImhPreferNumbers
    selectByMouse: true
    Accessible.name: accessibleName
    Accessible.description: invalid ? errorText : ""

    onPressed: field.openCalendar(false)
    onAccepted: calendar.close()        // Enter：頁面送出查詢；月曆收起來
    onActiveFocusChanged: if (!activeFocus) Qt.callLater(field.closeIfFocusLeft)
    onTextChanged: if (calendar.visible) field.follow(field.parse(field.text))

    Keys.onDownPressed: field.openCalendar(true)
    Keys.onEscapePressed: (event) => {
        if (calendar.visible)
            calendar.close()
        else
            event.accepted = false      // 沒有月曆時不吃掉 Esc（例如對話框要用）
    }

    // ── 日期的小工具：一律用當地時間的中午，換日、夏令時間都不會差一天

    function pad(number) {
        return (number < 10 ? "0" : "") + number
    }

    function format(date) {
        return date.getFullYear() + "-" + pad(date.getMonth() + 1) + "-" + pad(date.getDate())
    }

    // "YYYY-MM-DD" → Date；格式不對、沒有這一天（2026-02-30）都是 null
    function parse(value) {
        const parts = (value || "").trim().split("-")
        if (parts.length !== 3 || parts[0].length !== 4 || parts[1].length !== 2 || parts[2].length !== 2)
            return null
        const numbers = parts.map(part => Number(part))
        if (numbers.some(number => !Number.isInteger(number)) || numbers[0] < 1000)
            return null
        const date = new Date(numbers[0], numbers[1] - 1, numbers[2], 12)
        return date.getFullYear() === numbers[0] && date.getMonth() === numbers[1] - 1 && date.getDate() === numbers[2]
            ? date : null
    }

    function day(year, month, date) {
        return new Date(year, month, date, 12)
    }

    function todayDate() {
        const fixed = parse(today)
        if (fixed)
            return fixed
        const now = new Date()
        return day(now.getFullYear(), now.getMonth(), now.getDate())
    }

    function sameDay(a, b) {
        return !!a && !!b && a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth()
               && a.getDate() === b.getDate()
    }

    function daysIn(year, month) {
        return new Date(year, month + 1, 0, 12).getDate()
    }

    // 「Sunday, November 1, 2026」：日期格的名稱、鍵盤移動時唸的字
    function spoken(date) {
        return calendarLocale.toString(date, qsTr("dddd, MMMM d, yyyy"))
    }

    // ── 月曆

    QtObject {
        id: internal
        property int year: 2026
        property int month: 0               // 0～11
        property var cursor: null           // 鍵盤停在哪一天
    }

    // 月曆顯示 date 那個月，鍵盤停在那一天
    function follow(date) {
        if (!date)
            return
        internal.cursor = date
        internal.year = date.getFullYear()
        internal.month = date.getMonth()
    }

    // 打開月曆：顯示欄位裡的日期（沒有、或打錯了就是今天）。intoCalendar：焦點進到日期上（↓、月曆圖示）
    function openCalendar(intoCalendar) {
        follow(parse(text) || todayDate())
        if (!calendar.visible)
            calendar.open()
        if (intoCalendar)
            grid.forceActiveFocus(Qt.TabFocusReason)
    }

    function closeCalendar() {
        calendar.close()
        field.forceActiveFocus()
    }

    // 焦點離開欄位和月曆（Tab 走了、點了別的地方）：月曆收起來
    function closeIfFocusLeft() {
        if (calendar.visible && !field.activeFocus && !calendar.activeFocus)
            calendar.close()
    }

    // 選了一天、Today、Clear：和打字一樣
    function choose(value) {
        text = value
        closeCalendar()
        field.textEdited()
    }

    function chooseRange(start, end) {
        closeCalendar()
        field.rangeChosen(format(start), format(end))
    }

    function chooseToday() {
        choose(format(todayDate()))
    }

    function chooseLast7Days() {
        const end = todayDate()
        chooseRange(day(end.getFullYear(), end.getMonth(), end.getDate() - 6), end)    // 含今天共 7 天
    }

    function chooseThisMonth() {
        const end = todayDate()
        chooseRange(day(end.getFullYear(), end.getMonth(), 1), end)     // 這個月 1 號到今天
    }

    function showMonth(year, month) {
        const first = day(year, month, 1)      // month 可以是 -1、12：Date 會換到前一年、下一年
        const cursor = internal.cursor || first
        follow(day(first.getFullYear(), first.getMonth(),
                   Math.min(cursor.getDate(), daysIn(first.getFullYear(), first.getMonth()))))
    }

    function moveCursor(days) {
        const cursor = internal.cursor || todayDate()
        follow(day(cursor.getFullYear(), cursor.getMonth(), cursor.getDate() + days))
        announce(spoken(internal.cursor))
    }

    function moveMonth(months) {
        showMonth(internal.year, internal.month + months)
        announce(spoken(internal.cursor))
    }

    function announce(text) {
        announced = announced.concat([text])
        Accessible.announce(text, Accessible.Polite)
    }

    // 月曆圖示：點了打開月曆，焦點進到日期上。不在 Tab 的順序裡（欄位按 ↓ 一樣能打開）
    AbstractButton {
        objectName: "calendarButton"
        x: 4
        width: 36
        height: parent.height
        focusPolicy: Qt.NoFocus
        Accessible.role: Accessible.Button
        Accessible.name: qsTr("Choose a date")
        onClicked: field.openCalendar(true)

        HoverHandler {
            cursorShape: Qt.PointingHandCursor
        }
    }

    background: Rectangle {
        radius: 12
        color: Theme.field
        border.width: field.activeFocus || field.invalid ? 2 : 1
        border.color: field.invalid ? Theme.errorOnDark : field.activeFocus ? Theme.focusOnDark : Theme.fieldBorder

        Image {
            x: 14
            anchors.verticalCenter: parent.verticalCenter
            width: 18
            height: 18
            sourceSize: Qt.size(18, 18)
            source: "../components/icons/calendar_iconNeutral.svg"
            Accessible.ignored: true
        }
    }

    // 快捷（Today、Last 7 days、This month、Clear）：整塊可以點，44 高
    component QuickOption: AbstractButton {
        id: option
        Layout.fillWidth: true
        implicitWidth: Math.max(120, label.implicitWidth + 2 * Theme.spaceS)
        implicitHeight: Theme.controlHeight
        focusPolicy: Qt.StrongFocus
        hoverEnabled: true
        Accessible.role: Accessible.Button
        Accessible.name: text
        Keys.onReturnPressed: option.click()
        Keys.onEnterPressed: option.click()

        contentItem: Text {
            id: label
            leftPadding: Theme.spaceS
            text: option.text
            color: Theme.textPrimary
            font.pixelSize: Theme.fontCaption
            verticalAlignment: Text.AlignVCenter
        }

        background: Rectangle {
            radius: 8
            color: option.down ? Theme.navItemPressed : option.hovered ? Theme.tile : "transparent"
            border.color: Theme.focusOnDark
            border.width: option.visualFocus ? 2 : 0
        }
    }

    // 上一個月、下一個月
    component MonthStep: AbstractButton {
        id: step
        property url iconSource: ""
        implicitWidth: 36
        implicitHeight: Theme.controlHeight
        focusPolicy: Qt.StrongFocus
        hoverEnabled: true
        Accessible.role: Accessible.Button
        Accessible.name: text
        Keys.onReturnPressed: step.click()
        Keys.onEnterPressed: step.click()

        contentItem: Item {
            Image {
                anchors.centerIn: parent
                width: Theme.iconS
                height: Theme.iconS
                sourceSize: Qt.size(Theme.iconS, Theme.iconS)
                source: step.iconSource
                Accessible.ignored: true
            }
        }

        background: Rectangle {
            radius: 10
            color: step.down ? Theme.actionSecondaryPressed : step.hovered ? Theme.actionSecondaryHover : "transparent"
            border.color: Theme.focusOnDark
            border.width: step.visualFocus ? 2 : 0
        }
    }

    Popup {
        id: calendar
        objectName: "calendarPopup"
        y: field.height + 6
        z: field.popupZ
        margins: Theme.spaceS               // 放不下時推回視窗裡
        padding: Theme.spaceM
        modal: false
        focus: false                        // 點欄位打開時，焦點留在欄位上，照樣可以打字
        // 點了月曆和欄位以外的地方就收起來；Esc 由下面的 Keys 處理（焦點要回到欄位）
        closePolicy: Popup.CloseOnPressOutsideParent
        onActiveFocusChanged: if (!activeFocus) Qt.callLater(field.closeIfFocusLeft)

        background: Rectangle {
            color: Theme.card
            radius: Theme.radiusTile
            border.color: Theme.tileBorder
            border.width: 1
        }

        contentItem: RowLayout {
            spacing: Theme.spaceM
            Accessible.role: Accessible.Dialog
            Accessible.name: qsTr("Choose a date")
            Keys.onEscapePressed: field.closeCalendar()

            ColumnLayout {
                objectName: "quickOptions"
                Layout.alignment: Qt.AlignTop
                spacing: 2

                QuickOption {
                    id: todayOption
                    objectName: "todayOption"
                    text: qsTr("Today")
                    onClicked: field.chooseToday()
                    KeyNavigation.tab: field.rangeOptions ? last7Option : clearOption
                    KeyNavigation.backtab: grid
                }
                QuickOption {
                    id: last7Option
                    objectName: "last7DaysOption"
                    visible: field.rangeOptions
                    text: qsTr("Last 7 days")
                    onClicked: field.chooseLast7Days()
                    KeyNavigation.tab: thisMonthOption
                    KeyNavigation.backtab: todayOption
                }
                QuickOption {
                    id: thisMonthOption
                    objectName: "thisMonthOption"
                    visible: field.rangeOptions
                    text: qsTr("This month")
                    onClicked: field.chooseThisMonth()
                    KeyNavigation.tab: clearOption
                    KeyNavigation.backtab: last7Option
                }
                Rectangle {     // Clear 和上面的分開：它是拿掉條件，不是選一天
                    Layout.fillWidth: true
                    Layout.topMargin: 4
                    Layout.bottomMargin: 4
                    implicitHeight: 1
                    color: Theme.cardBorder
                }
                QuickOption {
                    id: clearOption
                    objectName: "clearOption"
                    text: qsTr("Clear")
                    onClicked: field.choose("")
                    KeyNavigation.tab: previousMonth
                    KeyNavigation.backtab: field.rangeOptions ? thisMonthOption : todayOption
                }
            }

            Rectangle {
                Layout.fillHeight: true
                implicitWidth: 1
                color: Theme.cardBorder
            }

            ColumnLayout {
                spacing: Theme.spaceS

                // 上一個月、Month、Year、下一個月
                RowLayout {
                    Layout.preferredWidth: grid.width
                    spacing: 4

                    MonthStep {
                        id: previousMonth
                        objectName: "previousMonthButton"
                        text: qsTr("Previous month")
                        iconSource: "../components/icons/chevron-left_actionSecondaryText.svg"
                        onClicked: field.moveMonth(-1)
                        KeyNavigation.tab: monthSelect
                        KeyNavigation.backtab: clearOption
                    }

                    HomeSelect {
                        id: monthSelect
                        objectName: "monthSelect"
                        Layout.fillWidth: true
                        popupZ: field.popupZ + 1          // 清單疊在月曆上面（月曆在對話框裡時也是）
                        leftPadding: 12
                        font.weight: Font.Normal
                        Accessible.name: qsTr("Month")
                        options: Array.from({ length: 12 }, (_, month) => ({
                            value: String(month), text: field.calendarLocale.standaloneMonthName(month, Locale.LongFormat)
                        }))
                        value: String(internal.month)
                        onPicked: (value) => field.showMonth(internal.year, Number(value))
                        KeyNavigation.tab: yearSelect
                        KeyNavigation.backtab: previousMonth
                    }

                    HomeSelect {
                        id: yearSelect
                        objectName: "yearSelect"
                        Layout.preferredWidth: 92
                        popupZ: field.popupZ + 1
                        leftPadding: 12
                        font.weight: Font.Normal
                        font.features: { "tnum": 1 }
                        Accessible.name: qsTr("Year")
                        // 前 10 年到明年；月曆顯示的年份不在這段時也放進去
                        options: {
                            const now = field.todayDate().getFullYear()
                            const first = Math.min(now - 10, internal.year)
                            const last = Math.max(now + 1, internal.year)
                            return Array.from({ length: last - first + 1 }, (_, i) => ({
                                value: String(first + i), text: String(first + i)
                            }))
                        }
                        value: String(internal.year)
                        onPicked: (value) => field.showMonth(Number(value), internal.month)
                        KeyNavigation.tab: nextMonth
                        KeyNavigation.backtab: monthSelect
                    }

                    MonthStep {
                        id: nextMonth
                        objectName: "nextMonthButton"
                        text: qsTr("Next month")
                        iconSource: "../components/icons/chevron-right_actionSecondaryText.svg"
                        onClicked: field.moveMonth(1)
                        KeyNavigation.tab: grid
                        KeyNavigation.backtab: yearSelect
                    }
                }

                DayOfWeekRow {
                    objectName: "dayOfWeekRow"
                    Layout.preferredWidth: grid.width
                    locale: field.calendarLocale
                    spacing: 0
                    padding: 0
                    Accessible.ignored: true

                    delegate: Text {
                        required property string shortName
                        text: shortName.slice(0, 2)             // Su Mo Tu We Th Fr Sa
                        color: Theme.textMuted
                        font.pixelSize: Theme.fontLabel
                        font.weight: Font.DemiBold
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                    }
                }

                MonthGrid {
                    id: grid
                    objectName: "monthGrid"
                    implicitWidth: 7 * Theme.controlHeight
                    implicitHeight: 6 * Theme.controlHeight
                    Layout.preferredWidth: implicitWidth
                    Layout.preferredHeight: implicitHeight
                    locale: field.calendarLocale
                    year: internal.year
                    month: internal.month
                    spacing: 0
                    padding: 0
                    focus: true
                    activeFocusOnTab: true
                    Accessible.role: Accessible.Table
                    Accessible.name: internal.cursor ? field.spoken(internal.cursor) : qsTr("Choose a date")
                    KeyNavigation.tab: todayOption
                    KeyNavigation.backtab: nextMonth

                    Keys.onPressed: (event) => {
                        const cursor = internal.cursor || field.todayDate()
                        switch (event.key) {
                        case Qt.Key_Left: field.moveCursor(-1); break
                        case Qt.Key_Right: field.moveCursor(1); break
                        case Qt.Key_Up: field.moveCursor(-7); break
                        case Qt.Key_Down: field.moveCursor(7); break
                        case Qt.Key_PageUp: field.moveMonth(-1); break
                        case Qt.Key_PageDown: field.moveMonth(1); break
                        case Qt.Key_Home: field.moveCursor(1 - cursor.getDate()); break
                        case Qt.Key_End:
                            field.moveCursor(field.daysIn(cursor.getFullYear(), cursor.getMonth()) - cursor.getDate())
                            break
                        case Qt.Key_Return:
                        case Qt.Key_Enter:
                        case Qt.Key_Space:
                            field.choose(field.format(cursor))
                            break
                        default:
                            return
                        }
                        event.accepted = true
                    }

                    delegate: AbstractButton {
                        id: cell
                        required property int day
                        required property int month
                        required property int year
                        readonly property var date: field.day(year, month, day)
                        readonly property bool inMonth: month === grid.month
                        readonly property bool isToday: field.sameDay(date, field.todayDate())
                        readonly property bool selected: field.sameDay(date, field.parse(field.text))
                        readonly property bool isCursor: field.sameDay(date, internal.cursor)

                        objectName: "day-" + field.format(date)
                        implicitWidth: Theme.controlHeight
                        implicitHeight: Theme.controlHeight
                        focusPolicy: Qt.NoFocus            // 鍵盤停在 MonthGrid 上，用 cursor 表示在哪一天
                        hoverEnabled: true
                        Accessible.role: Accessible.Button
                        Accessible.name: field.spoken(date) + (isToday ? qsTr(", today") : "")
                                         + (selected ? qsTr(", selected") : "")
                        onClicked: field.choose(field.format(date))

                        contentItem: Item {
                            Text {
                                anchors.centerIn: parent
                                text: cell.day
                                color: cell.selected ? Theme.actionPrimaryText
                                     : cell.inMonth ? Theme.textPrimary : Theme.textDisabled
                                font.pixelSize: Theme.fontCaption
                                font.weight: cell.isToday || cell.selected ? Font.Bold : Font.Normal
                                font.features: { "tnum": 1 }
                            }

                            Rectangle {     // 今天：數字下面一個點
                                objectName: "todayMark"
                                visible: cell.isToday
                                anchors.horizontalCenter: parent.horizontalCenter
                                y: parent.height - 10
                                width: 4
                                height: 4
                                radius: 2
                                color: cell.selected ? Theme.actionPrimaryText : Theme.textSecondary
                            }
                        }

                        background: Rectangle {
                            anchors.fill: parent
                            anchors.margins: 2
                            radius: 10
                            color: cell.selected ? Theme.actionPrimary
                                 : cell.down ? Theme.navItemPressed : cell.hovered ? Theme.tile : "transparent"

                            Rectangle {     // 鍵盤停在這一天（DS-30 的焦點框）
                                objectName: "cursorRing"
                                visible: cell.isCursor && grid.activeFocus
                                anchors.fill: parent
                                anchors.margins: -2
                                radius: parent.radius + 2
                                color: "transparent"
                                border.color: Theme.focusOnDark
                                border.width: 2
                            }
                        }
                    }
                }
            }
        }
    }
}
