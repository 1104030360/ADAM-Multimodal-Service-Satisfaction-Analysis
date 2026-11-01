import QtQuick
import QtQuick.Layouts
// 自訂了輸入框和選單的 background，要用 Basic 樣式（T13）
import QtQuick.Controls.Basic
import "../components"

// 新增帳號（UX-33、UX-34、DS-24、DS-27、DS-52；設計稿「新增帳號」）。
// - 按下送出才檢查空白欄位（accountManager.addAccount），錯誤寫在欄位下方，焦點移到第一個有錯的欄位。打字時不跳錯誤。
// - 送出期間按鈕忙碌（Adding…），不會重複送出。結果一定看得到：成功是 toast、清空表單、焦點回到 Username；
//   帳號重複是 Username 的欄位錯誤；連不上資料庫是常駐的錯誤 toast，可以重試，表單內容留著。
// - 等級只列出能指派的（1 · Staff、2 · Admin…），Python 端照樣檢查（PLAN D9）。
// - 「Update Account」先拿掉（PLAN D8）：畫面上沒有帳號清單可以選。Python 的 updateAccount 還在。
Rectangle {
    id: root
    color: Theme.contentBg

    readonly property string profileState: adminHome ? adminHome.profile.state : "loading"
    readonly property int level: accountManager ? accountManager.currentLevel : 0
    // 選單的選項：{ value: 等級, text: "1 · Staff" }
    readonly property var levelOptions: (accountManager ? accountManager.assignableLevels : [])
        .map(value => ({ value: value, text: levelLabel(value) }))

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

    // 一個欄位：標籤、輸入框、欄位錯誤（先留好一行的高度，錯誤出現時版面不跳）
    component LabeledField: ColumnLayout {
        id: field

        property string label: ""
        property string error: ""
        property alias text: input.text
        property alias echoMode: input.echoMode
        property alias maximumLength: input.maximumLength
        property alias input: input

        signal edited()
        signal submitted()

        Layout.fillWidth: true
        spacing: 8

        Label {
            text: field.label
            color: Theme.textPrimary
            font.pixelSize: Theme.fontBody
            font.weight: Font.DemiBold
        }

        TextField {
            id: input
            Layout.fillWidth: true
            Layout.preferredHeight: 44
            leftPadding: 12
            rightPadding: 12
            color: Theme.textPrimary
            font.pixelSize: Theme.fontBodyLarge
            Accessible.name: field.label
            Accessible.description: field.error
            onTextEdited: field.edited()
            onAccepted: field.submitted()     // Enter 送出

            background: Rectangle {
                radius: Theme.radiusS
                color: Theme.contentBg
                border.width: field.error !== "" || input.activeFocus ? 2 : 1
                border.color: field.error !== "" ? Theme.errorOnLight
                            : input.activeFocus ? Theme.focusOnLight : Theme.textSecondary
            }
        }

        FieldError {
            Layout.fillWidth: true
            text: field.error
        }
    }

    Flickable {
        anchors.fill: parent
        contentWidth: width
        contentHeight: form.implicitHeight + 2 * 48
        clip: true
        boundsBehavior: Flickable.StopAtBounds

        ColumnLayout {
            id: form
            x: (parent.width - width) / 2
            y: 48
            width: Math.min(640, root.width - 2 * Theme.space3XL)
            spacing: Theme.spaceM

            ColumnLayout {
                Layout.fillWidth: true
                Layout.bottomMargin: Theme.spaceM
                spacing: 4

                Text {
                    text: qsTr("Add Account")
                    color: Theme.textPrimary
                    font.pixelSize: Theme.fontHeadline
                    font.weight: Font.Bold
                    Accessible.role: Accessible.Heading
                    Accessible.name: text
                }

                Text {
                    text: qsTr("Add a staff or admin account.")
                    color: Theme.textSecondary
                    font.pixelSize: Theme.fontCaption
                }
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
                    echoMode: TextInput.Password
                    onEdited: accountManager.clearFieldError("password")
                    onSubmitted: root.submit()
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

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 8

                    Label {
                        text: qsTr("Level")
                        color: Theme.textPrimary
                        font.pixelSize: Theme.fontBody
                        font.weight: Font.DemiBold
                    }

                    // 只列出能指派的等級（DS-51）；還不知道自己的等級時是空的、停用
                    ComboBox {
                        id: levelField
                        objectName: "levelField"
                        Layout.fillWidth: true
                        Layout.preferredHeight: 44
                        model: root.levelOptions
                        textRole: "text"
                        valueRole: "value"
                        enabled: count > 0
                        font.pixelSize: Theme.fontBodyLarge
                        displayText: count > 0 ? currentText : qsTr("Loading…")
                        Accessible.name: qsTr("Level")
                        Accessible.description: levelError.hasError ? levelError.text : levelHelp.text
                        onActivated: accountManager.clearFieldError("level")

                        background: Rectangle {
                            implicitHeight: 44
                            radius: Theme.radiusS
                            color: levelField.enabled ? Theme.contentBg : Theme.surfaceMuted
                            border.width: levelError.hasError || levelField.activeFocus ? 2 : 1
                            border.color: levelError.hasError ? Theme.errorOnLight
                                        : levelField.activeFocus ? Theme.focusOnLight : Theme.textSecondary
                        }
                    }

                    // 欄位錯誤（等級太高）和說明放在同一行的位置：有錯誤時顯示錯誤
                    Item {
                        Layout.fillWidth: true
                        implicitHeight: levelError.hasError ? levelError.implicitHeight : levelHelp.implicitHeight

                        FieldError {
                            id: levelError
                            objectName: "levelError"
                            width: parent.width
                            visible: hasError
                            text: accountManager.levelError
                        }

                        Text {
                            id: levelHelp
                            objectName: "levelHelp"
                            width: parent.width
                            visible: !levelError.hasError
                            text: root.level > 0
                                  ? qsTr("You can assign levels 1–%1 (your level is %1). Admins can add accounts up to their own level.").arg(root.level)
                                  : qsTr("Loading your account level…")
                            color: Theme.textSecondary
                            font.pixelSize: Theme.fontCaption
                            lineHeight: 20
                            lineHeightMode: Text.FixedHeight
                            wrapMode: Text.Wrap
                        }
                    }
                }

                LabeledField {
                    id: jobTitleField
                    objectName: "jobTitleField"
                    label: qsTr("Job Title")
                    maximumLength: 100      // account_occupation_name VARCHAR(100)；可以空白
                    onSubmitted: root.submit()
                }

                RowLayout {
                    Layout.topMargin: 8
                    spacing: Theme.spaceM

                    BusyButton {
                        id: addButton
                        objectName: "addAccountButton"
                        variant: "primary"
                        large: true
                        text: qsTr("Add Account")
                        busyText: qsTr("Adding…")
                        busy: accountManager.busy
                        onClicked: root.submit()
                    }
                }
            }
        }
    }
}
