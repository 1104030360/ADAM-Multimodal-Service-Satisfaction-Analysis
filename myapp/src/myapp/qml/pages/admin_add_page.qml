pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Layouts
// 自訂了輸入框、選單、按鈕的 background，要用 Basic 樣式（T13）
import QtQuick.Controls.Basic
import "../components"

// 新增帳號（UX-33、UX-34、DS-24、DS-27、DS-52；新版介面 PLAN 6-8，設計稿 AdminAddAccount）。
// 頁面標題和說明在外殼的頁首；這裡是「New account」表單卡，旁邊一張「Account levels」小卡（frontend-rebuild PLAN D9）。
// - 按下送出才檢查空白欄位（accountManager.addAccount），錯誤寫在欄位下方，焦點移到第一個有錯的欄位。打字時不跳錯誤。
// - 送出期間按鈕忙碌（Adding…），不會重複送出。結果一定看得到：成功是 toast、清空表單、焦點回到 Username；
//   帳號重複是 Username 的欄位錯誤；連不上資料庫是常駐的錯誤 toast，可以重試，表單內容留著。
// - 密碼可以顯示出來核對（Show／Hide）；下面說明要當面交給對方：資料庫只存雜湊，之後在 app 裡看不到。
// - 等級只列出能指派的（1 · Staff、2 · Admin…），Python 端照樣檢查（PLAN D9）。
// - 一進來焦點就在 Username（T25：這一頁是 DS-29 的例外，換過來就是要打字）。
// - 「Update Account」先拿掉（PLAN D8）：畫面上沒有帳號清單可以選。Python 的 updateAccount 還在。
Item {
    id: root

    readonly property string profileState: adminHome ? adminHome.profile.state : "loading"
    readonly property int level: accountManager ? accountManager.currentLevel : 0
    // 選單的選項：{ value: 等級, text: "1 · Staff" }
    readonly property var levelOptions: (accountManager ? accountManager.assignableLevels : [])
        .map(value => ({ value: value, text: levelLabel(value) }))
    property bool passwordShown: false

    // 版面（設計稿）：表單卡 520～760、說明卡 280～420，中間 24；放不下兩張時說明卡排到表單下面
    readonly property int gap: Theme.sectionGap
    readonly property bool sideBySide: width >= 520 + gap + 280
    readonly property real extra: Math.max(0, width - (520 + gap + 280))
    readonly property int formWidth: sideBySide ? Math.min(760, 520 + extra * 2 / 3) : Math.min(760, width)
    readonly property int levelsWidth: sideBySide ? Math.min(420, 280 + extra / 3) : formWidth

    function levelLabel(value) {
        return value === 1 ? qsTr("%1 · Staff").arg(value) : qsTr("%1 · Admin").arg(value)
    }

    function submit() {
        if (accountManager.busy)    // 輔助技術的「按下」不經過 BusyButton 的擋板
            return
        accountManager.addAccount(usernameField.text, passwordField.text, nameField.text,
                                  levelField.currentIndex >= 0 ? String(levelField.currentValue) : "",
                                  jobTitleField.text)
    }

    function clearForm() {
        usernameField.text = ""
        passwordField.text = ""
        nameField.text = ""
        jobTitleField.text = ""
        passwordShown = false       // 下一個帳號的密碼預設不顯示
        levelField.currentIndex = levelField.count > 0 ? 0 : -1
        usernameField.input.forceActiveFocus()
    }

    function focusField(field) {
        const target = field === "username" ? usernameField.input
                     : field === "password" ? passwordField.input
                     : field === "name" ? nameField.input
                     : levelField
        target.forceActiveFocus()
    }

    Connections {
        target: accountManager
        function onAccountAdded(ok, message) {
            if (ok)
                root.clearForm()
        }
        function onFocusRequested(field) {
            root.focusField(field)
        }
    }

    Component.onCompleted: usernameField.input.forceActiveFocus()

    // 欄位的標籤：14 px、textSecondary
    component FieldLabel: Text {
        color: Theme.textSecondary
        font.pixelSize: Theme.fontCaption
        font.weight: Font.DemiBold
        textFormat: Text.PlainText
    }

    // 欄位下面的一行：有錯誤時是錯誤（DS-27），沒有時是說明（可以沒有）。先保留一行的高度，錯誤出現時版面不跳
    component HelpOrError: Item {
        id: slot

        property string error: ""
        property string help: ""
        property string errorName: ""       // 測試用的 objectName
        property string helpName: ""

        implicitHeight: fieldError.hasError || help === "" ? fieldError.implicitHeight : helpText.implicitHeight

        FieldError {
            id: fieldError
            objectName: slot.errorName
            width: parent.width
            visible: hasError
            text: slot.error
        }

        Text {
            id: helpText
            objectName: slot.helpName
            width: parent.width
            visible: !fieldError.hasError && text !== ""
            text: slot.help
            color: Theme.textMuted
            font.pixelSize: Theme.fontSmall
            lineHeight: fieldError.lineHeight      // 和欄位錯誤一樣高：錯誤換掉說明時，下面的欄位不動
            lineHeightMode: Text.FixedHeight
            wrapMode: Text.Wrap
        }
    }

    // 一個欄位：標籤、輸入框（右邊可以再放按鈕）、欄位錯誤或說明
    component LabeledField: ColumnLayout {
        id: field

        property string label: ""
        property bool optional: false
        property string error: ""
        property string help: ""
        property string helpName: ""
        property alias text: input.text
        property alias echoMode: input.echoMode
        property alias maximumLength: input.maximumLength
        property alias input: input
        default property alias trailing: inputRow.data

        signal edited()
        signal submitted()

        Layout.fillWidth: true
        spacing: 8

        Row {
            spacing: 6
            FieldLabel { text: field.label }
            FieldLabel {
                visible: field.optional
                text: qsTr("(optional)")
                color: Theme.textFaint
                font.weight: Font.Normal
            }
        }

        RowLayout {
            id: inputRow
            Layout.fillWidth: true
            spacing: 8

            TextField {
                id: input
                Layout.fillWidth: true
                Layout.preferredHeight: Theme.controlHeight
                leftPadding: 14
                rightPadding: 14
                color: Theme.textPrimary
                selectionColor: Theme.chromeSelection
                selectedTextColor: Theme.textOnDark
                font.pixelSize: Theme.fontInput
                selectByMouse: true
                Accessible.name: field.label
                Accessible.description: field.error !== "" ? field.error : field.help
                onTextEdited: field.edited()
                onAccepted: field.submitted()     // Enter 送出

                background: Rectangle {
                    radius: 12
                    color: Theme.field
                    border.width: field.error !== "" || input.activeFocus ? 2 : 1
                    border.color: field.error !== "" ? Theme.errorOnDark
                                : input.activeFocus ? Theme.focusOnDark : Theme.fieldBorder
                }
            }
        }

        HelpOrError {
            Layout.fillWidth: true
            error: field.error
            help: field.help
            helpName: field.helpName
        }
    }

    // 深色的下拉選單：框線、展開的清單都取自 Theme（Basic 預設是淺色）
    component Select: ComboBox {
        id: select

        property bool invalid: false

        implicitHeight: Theme.controlHeight
        font.pixelSize: Theme.fontInput
        focusPolicy: Qt.StrongFocus

        contentItem: Text {
            leftPadding: 14
            rightPadding: select.indicator.width + 14
            text: select.displayText
            font: select.font
            color: select.enabled ? Theme.textPrimary : Theme.textDisabled
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
            Accessible.ignored: true
        }

        indicator: Image {
            x: select.width - width - 14
            y: (select.height - height) / 2
            width: Theme.iconS
            height: Theme.iconS
            sourceSize: Qt.size(Theme.iconS, Theme.iconS)
            source: "../components/icons/chevron-down_iconNeutral.svg"
            Accessible.ignored: true
        }

        background: Rectangle {
            implicitWidth: 200
            implicitHeight: Theme.controlHeight
            radius: 12
            color: select.enabled ? Theme.field : Theme.tile
            border.width: select.invalid || select.activeFocus ? 2 : 1
            border.color: select.invalid ? Theme.errorOnDark : select.activeFocus ? Theme.focusOnDark : Theme.fieldBorder
        }

        delegate: ItemDelegate {
            id: option
            required property var model
            required property int index
            width: ListView.view ? ListView.view.width : 0
            height: Theme.controlHeight
            highlighted: select.highlightedIndex === index
            Accessible.name: option.model[select.textRole]

            contentItem: Text {
                leftPadding: 10
                text: option.model[select.textRole]
                color: Theme.textPrimary
                font: select.font
                verticalAlignment: Text.AlignVCenter
                elide: Text.ElideRight
            }

            background: Rectangle {
                radius: 8
                color: option.highlighted ? Theme.tileSelected : "transparent"
            }
        }

        popup: Popup {
            y: select.height + 4
            width: select.width
            implicitHeight: Math.min(contentItem.implicitHeight + 2 * padding, 320)
            padding: 4

            contentItem: ListView {
                clip: true
                implicitHeight: contentHeight
                model: select.popup.visible ? select.delegateModel : null
                currentIndex: select.highlightedIndex
                ScrollIndicator.vertical: ScrollIndicator {}
            }

            background: Rectangle {
                radius: 12
                color: Theme.card
                border.width: 1
                border.color: Theme.tileBorder
            }
        }
    }

    Flickable {
        id: scroller
        anchors.fill: parent
        contentWidth: width
        contentHeight: root.sideBySide ? Math.max(formCard.height, levelsCard.height)
                                       : levelsCard.y + levelsCard.height
        boundsBehavior: Flickable.StopAtBounds
        clip: true
        ScrollBar.vertical: ScrollBar {}

        Card {
            id: formCard
            objectName: "formCard"
            width: root.formWidth
            bottomPadding: 28

            ColumnLayout {
                width: parent.width
                spacing: 12

                Text {
                    Layout.bottomMargin: 8
                    text: qsTr("New account")
                    color: Theme.textTitle
                    font.pixelSize: Theme.fontTitleSmall
                    font.weight: Font.Bold
                    Accessible.role: Accessible.Heading
                    Accessible.name: text
                }

                // 自己的等級沒有載入，就不知道能指派哪些等級：表單換成區塊錯誤（DS-27）
                InlineError {
                    id: profileError
                    objectName: "profileError"
                    Layout.fillWidth: true
                    visible: root.profileState === "error"
                    title: qsTr("Your account details didn't load")
                    detail: adminHome ? adminHome.profile.errorDetail : ""
                    technicalDetail: adminHome ? adminHome.profile.errorTechnical : ""
                    failureCount: adminHome ? adminHome.profile.failureCount : 0
                    onRetry: adminHome.profile.load()
                }

                ColumnLayout {
                    id: fields
                    Layout.fillWidth: true
                    visible: !profileError.visible
                    spacing: Theme.spaceM

                    LabeledField {
                        id: usernameField
                        objectName: "usernameField"
                        label: qsTr("Username")
                        error: accountManager.usernameError
                        maximumLength: 64       // tbl_account.account_username VARCHAR(64)
                        onEdited: accountManager.clearFieldError("username")
                        onSubmitted: root.submit()
                    }

                    LabeledField {
                        id: passwordField
                        objectName: "passwordField"
                        label: qsTr("Password")
                        error: accountManager.passwordError
                        help: qsTr("Hand it over in person. It can't be seen in the app later.")
                        helpName: "passwordHelp"
                        echoMode: root.passwordShown ? TextInput.Normal : TextInput.Password
                        onEdited: accountManager.clearFieldError("password")
                        onSubmitted: root.submit()

                        // 顯示密碼，打完可以核對；換下一個帳號時又藏起來
                        Button {
                            id: showButton
                            objectName: "showPasswordButton"
                            Layout.preferredHeight: Theme.controlHeight
                            leftPadding: Theme.spaceM
                            rightPadding: Theme.spaceM
                            focusPolicy: Qt.StrongFocus
                            text: root.passwordShown ? qsTr("Hide") : qsTr("Show")
                            font.pixelSize: Theme.fontCaption
                            font.weight: Font.DemiBold
                            Accessible.name: root.passwordShown ? qsTr("Hide password") : qsTr("Show password")
                            Keys.onReturnPressed: click()
                            Keys.onEnterPressed: click()
                            onClicked: root.passwordShown = !root.passwordShown

                            // Show、Hide 取比較寬的那個：換字時按鈕不變寬，密碼欄不跟著縮放
                            TextMetrics { id: showMetrics; font: showButton.font; text: qsTr("Show") }
                            TextMetrics { id: hideMetrics; font: showButton.font; text: qsTr("Hide") }

                            contentItem: Item {
                                implicitWidth: 18 + 8 + Math.ceil(Math.max(showMetrics.advanceWidth, hideMetrics.advanceWidth))
                                implicitHeight: 18

                                Row {
                                    anchors.verticalCenter: parent.verticalCenter
                                    spacing: 8

                                    Image {
                                        anchors.verticalCenter: parent.verticalCenter
                                        width: 18
                                        height: 18
                                        sourceSize: Qt.size(18, 18)
                                        source: root.passwordShown ? "../components/icons/eye-off_actionSecondaryText.svg"
                                                                   : "../components/icons/eye_actionSecondaryText.svg"
                                        Accessible.ignored: true
                                    }

                                    Text {
                                        anchors.verticalCenter: parent.verticalCenter
                                        text: showButton.text
                                        font: showButton.font
                                        color: Theme.actionSecondaryText
                                        Accessible.ignored: true
                                    }
                                }
                            }

                            background: Rectangle {
                                implicitWidth: 64
                                radius: 12
                                color: showButton.down ? Theme.actionSecondaryPressed
                                     : showButton.hovered ? Theme.actionSecondaryHover : Theme.actionSecondaryBg
                                border.width: 1
                                border.color: Theme.actionSecondaryBorder

                                Rectangle {     // 鍵盤焦點框（DS-30）
                                    visible: showButton.visualFocus
                                    anchors.fill: parent
                                    anchors.margins: -4
                                    radius: parent.radius + 4
                                    color: "transparent"
                                    border.color: Theme.focusOnDark
                                    border.width: 2
                                }
                            }
                        }
                    }

                    LabeledField {
                        id: nameField
                        objectName: "nameField"
                        label: qsTr("Name")
                        error: accountManager.nameError
                        maximumLength: 100      // account_name VARCHAR(100)
                        onEdited: accountManager.clearFieldError("name")
                        onSubmitted: root.submit()
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Theme.spaceL

                        ColumnLayout {
                            Layout.fillWidth: true
                            Layout.preferredWidth: 1      // 和 Job Title 平分
                            Layout.alignment: Qt.AlignTop
                            spacing: 8

                            FieldLabel { text: qsTr("Level") }

                            // 只列出能指派的等級（DS-51）；還不知道自己的等級時是空的、停用
                            Select {
                                id: levelField
                                objectName: "levelField"
                                Layout.fillWidth: true
                                model: root.levelOptions
                                textRole: "text"
                                valueRole: "value"
                                enabled: count > 0
                                invalid: levelSlot.error !== ""
                                displayText: count > 0 ? currentText : qsTr("Loading…")
                                Accessible.name: qsTr("Level")
                                Accessible.description: levelSlot.error !== "" ? levelSlot.error : levelSlot.help
                                onActivated: accountManager.clearFieldError("level")
                            }

                            // 欄位錯誤（等級太高）和說明放在同一行的位置：有錯誤時顯示錯誤
                            HelpOrError {
                                id: levelSlot
                                Layout.fillWidth: true
                                error: accountManager.levelError
                                help: root.level > 0 ? qsTr("You can add levels 1–%1 (your level is %1).").arg(root.level)
                                                     : qsTr("Loading your account level…")
                                errorName: "levelError"
                                helpName: "levelHelp"
                            }
                        }

                        LabeledField {
                            id: jobTitleField
                            objectName: "jobTitleField"
                            Layout.preferredWidth: 1
                            Layout.alignment: Qt.AlignTop
                            label: qsTr("Job Title")
                            optional: true
                            maximumLength: 100      // account_occupation_name VARCHAR(100)；可以空白
                            onSubmitted: root.submit()
                        }
                    }

                    BusyButton {
                        id: addButton
                        objectName: "addAccountButton"
                        Layout.topMargin: 4
                        variant: "primary"
                        text: qsTr("Add Account")
                        busyText: qsTr("Adding…")
                        busy: accountManager.busy
                        onClicked: root.submit()
                    }
                }
            }
        }

        // 等級是什麼意思（frontend-rebuild PLAN D9）：1 是服務人員，2～5 都是管理員，差別只在能新增到哪個等級
        Card {
            id: levelsCard
            objectName: "levelsCard"
            x: root.sideBySide ? formCard.width + root.gap : 0
            y: root.sideBySide ? 0 : formCard.height + root.gap
            width: root.levelsWidth

            ColumnLayout {
                width: parent.width
                spacing: Theme.spaceM

                Text {
                    text: qsTr("Account levels")
                    color: Theme.textTitle
                    font.pixelSize: Theme.fontBody
                    font.weight: Font.Bold
                    Accessible.role: Accessible.Heading
                    Accessible.name: text
                }

                Repeater {
                    model: [
                        { title: qsTr("1 · Staff"), text: qsTr("Records services and sees only their own results.") },
                        { title: qsTr("2–5 · Admin"),
                          text: qsTr("Sees every staff member's services and scores. Can add accounts up to their own level.") }
                    ]

                    delegate: ColumnLayout {
                        id: entry
                        required property var modelData
                        Layout.fillWidth: true
                        spacing: 4

                        Text {
                            Layout.fillWidth: true
                            text: entry.modelData.title
                            color: Theme.textPrimary
                            font.pixelSize: Theme.fontCaption
                            font.weight: Font.DemiBold
                            wrapMode: Text.Wrap
                        }

                        Text {
                            Layout.fillWidth: true
                            text: entry.modelData.text
                            color: Theme.textMuted
                            font.pixelSize: Theme.fontSmall
                            lineHeight: 19
                            lineHeightMode: Text.FixedHeight
                            wrapMode: Text.Wrap
                        }
                    }
                }
            }
        }
    }
}
