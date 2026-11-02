pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Layouts
// 自訂了輸入框、按鈕的 background，要用 Basic 樣式（T13）
import QtQuick.Controls.Basic
import QtQuick.Dialogs

// 設定頁的 Account 區塊（new-feature PLAN 第 6 節 N3、13.7 S6；T36），兩端共用。
// - Profile Photo：Choose… 選一張圖（只有自己的照片，沒有地方選別的帳號）→ 預覽 → Save 或 Cancel；有照片時可以 Remove。
//   處理（轉正、裁成正方形、256×256 的 JPEG、去掉 EXIF）、上傳、寫資料庫都在背景（myapp/account.py）。
// - 摘要：Display Name、Username、Role（唯讀）、Password（不顯示）。按 Edit／Edit Username／Change Password 才展開表單，
//   一次只開一個；每個表單只送自己的欄位，Save／Cancel。Enter 送出、Esc 取消。
// - 密碼欄可以 Show／Hide；表單關掉、存好時清空（密碼不留在畫面上）。改登入帳號、密碼不會登出：下次登入時用新的。
// - 錯誤寫在欄位下面（FieldError），焦點移到那一格；連不上、在別處改過是表單最下面的錯誤，表單留著可以再按 Save。
// account：AccountSelfService（context property myAccount）。沒有時（例如還沒登入）整個區塊不顯示。
SettingsSection {
    id: section

    property var window: null       // 外殼的視窗（頁面的 setWindow）
    property var account: null
    property string openForm: ""    // ""、name、username、password
    property bool passwordShown: false

    readonly property var profile: account ? account.profile : null
    readonly property var errors: account ? account.errors : ({})
    readonly property bool profileReady: profile !== null && profile.state === "ready"
    readonly property string roleText: !profile || profile.level <= 0 ? ""
                                     : profile.level === 1 ? qsTr("Staff") : qsTr("Admin · level %1").arg(profile.level)
    // 頭像：選好還沒存的預覽 → 目前的照片 → 預設的圖示
    readonly property string avatarSource: account && account.previewSource !== "" ? account.previewSource
                                         : profile && profile.photoSource !== "" ? profile.photoSource : ""

    function openEditor(form) {
        closeForm()
        openForm = form
        if (account)
            account.clearForm(form)
        if (form === "name") {
            nameInput.text = profile ? profile.name : ""
            nameInput.input.forceActiveFocus()
            nameInput.input.selectAll()
        } else if (form === "username") {
            usernameInput.text = profile ? profile.username : ""
            usernameInput.input.forceActiveFocus()
            usernameInput.input.selectAll()
        } else if (form === "password") {
            currentPasswordInput.input.forceActiveFocus()
        }
    }

    // 收起表單：欄位全部清空（密碼不留在畫面上），錯誤拿掉，焦點回到打開它的按鈕
    function closeForm() {
        const form = openForm
        nameInput.text = ""
        usernameInput.text = ""
        usernamePasswordInput.text = ""
        currentPasswordInput.text = ""
        newPasswordInput.text = ""
        confirmPasswordInput.text = ""
        passwordShown = false
        openForm = ""
        if (account && form !== "")
            account.clearForm(form)
        return form
    }

    function cancelForm() {
        const form = closeForm()
        const button = form === "name" ? editNameButton : form === "username" ? editUsernameButton
                     : form === "password" ? changePasswordButton : null
        if (button)
            button.forceActiveFocus()
    }

    function saveName() {
        if (account && !account.nameBusy)
            account.rename(nameInput.text)
    }

    function saveUsername() {
        if (account && !account.usernameBusy)
            account.changeUsername(usernameInput.text, usernamePasswordInput.text)
    }

    function savePassword() {
        if (account && !account.passwordBusy)
            account.changePassword(currentPasswordInput.text, newPasswordInput.text, confirmPasswordInput.text)
    }

    function focusField(field) {
        const target = {
            name: nameInput.input, nameForm: nameInput.input,
            username: usernameInput.input, usernamePassword: usernamePasswordInput.input,
            usernameForm: usernameInput.input,
            currentPassword: currentPasswordInput.input, newPassword: newPasswordInput.input,
            confirmPassword: confirmPasswordInput.input, passwordForm: currentPasswordInput.input
        }[field]
        if (target && target.visible)
            target.forceActiveFocus()
    }

    title: qsTr("Account")
    visible: account !== null

    Connections {
        target: section.account
        function onSaved(form) {
            if (form === section.openForm) {
                section.cancelForm()
            }
        }
        function onFocusRequested(field) {
            section.focusField(field)
        }
    }

    FileDialog {
        id: photoDialog
        objectName: "photoDialog"
        title: qsTr("Choose a Profile Photo")
        fileMode: FileDialog.OpenFile
        nameFilters: [qsTr("Images (*.jpg *.jpeg *.png *.heic *.heif *.webp *.bmp *.gif *.tif *.tiff)")]
        onAccepted: {
            if (section.account)
                section.account.choosePhoto(selectedFile.toString())
        }
    }

    // ── 欄位的小元件
    component FieldLabel: Text {
        color: Theme.textSecondary
        font.pixelSize: Theme.fontCaption
        font.weight: Font.DemiBold
        textFormat: Text.PlainText
    }

    // 一個欄位：標籤、輸入框（右邊可以再放按鈕）、欄位錯誤
    component LabeledField: ColumnLayout {
        id: field

        property string label: ""
        property string error: ""
        property string help: ""
        property bool secret: false
        property alias text: textInput.text
        property alias maximumLength: textInput.maximumLength
        property alias input: textInput
        default property alias trailing: inputRow.data

        signal edited()
        signal submitted()
        signal cancelled()

        Layout.fillWidth: true
        spacing: 8

        FieldLabel { text: field.label }

        RowLayout {
            id: inputRow
            Layout.fillWidth: true
            spacing: 8

            TextField {
                id: textInput
                Layout.fillWidth: true
                Layout.preferredHeight: Theme.controlHeight
                leftPadding: 14
                rightPadding: 14
                color: Theme.textPrimary
                selectionColor: Theme.chromeSelection
                selectedTextColor: Theme.textOnDark
                font.pixelSize: Theme.fontInput
                selectByMouse: true
                echoMode: field.secret && !section.passwordShown ? TextInput.Password : TextInput.Normal
                inputMethodHints: field.secret ? Qt.ImhSensitiveData | Qt.ImhNoPredictiveText : Qt.ImhNone
                Accessible.name: field.label
                Accessible.description: field.error !== "" ? field.error : field.help
                onTextEdited: field.edited()
                onAccepted: field.submitted()     // Enter 送出
                Keys.onEscapePressed: field.cancelled()

                background: Rectangle {
                    radius: 12
                    color: Theme.field
                    border.width: field.error !== "" || textInput.activeFocus ? 2 : 1
                    border.color: field.error !== "" ? Theme.errorOnDark
                                : textInput.activeFocus ? Theme.focusOnDark : Theme.fieldBorder
                }
            }
        }

        FieldError {
            Layout.fillWidth: true
            visible: hasError
            text: field.error
        }

        Text {
            Layout.fillWidth: true
            visible: field.help !== "" && field.error === ""
            text: field.help
            color: Theme.textMuted
            font.pixelSize: Theme.fontSmall
            wrapMode: Text.Wrap
        }
    }

    // 摘要的一列：標籤、值、右邊的按鈕（可以沒有）
    component SummaryRow: RowLayout {
        id: row

        property string label: ""
        property string value: ""
        property string valueName: ""        // 給輔助技術唸的值（密碼唸 Hidden，不唸一串點）
        default property alias actions: actionSlot.data

        Layout.fillWidth: true
        Layout.minimumHeight: Theme.controlHeight
        spacing: Theme.spaceM
        Accessible.role: Accessible.StaticText
        Accessible.name: label + ": " + (valueName !== "" ? valueName : value)

        Text {
            Layout.preferredWidth: 140
            text: row.label
            color: Theme.textSecondary
            font.pixelSize: Theme.fontCaption
            font.weight: Font.DemiBold
            Accessible.ignored: true
        }

        Text {
            Layout.fillWidth: true
            text: row.value
            color: Theme.textPrimary
            font.pixelSize: Theme.fontBody
            elide: Text.ElideRight
            textFormat: Text.PlainText
            Accessible.ignored: true
        }

        RowLayout {
            id: actionSlot
            spacing: Theme.spaceS
        }
    }

    // 表單下面：Save（主要）、Cancel，表單層級的錯誤在上面
    component FormButtons: ColumnLayout {
        id: buttons

        property string error: ""
        property bool busy: false

        signal save()
        signal cancel()

        Layout.fillWidth: true
        spacing: Theme.spaceS

        FieldError {
            objectName: "formError"
            Layout.fillWidth: true
            visible: hasError
            text: buttons.error
        }

        RowLayout {
            spacing: Theme.spaceS

            BusyButton {
                objectName: "saveButton"
                variant: "primary"
                text: qsTr("Save")
                busyText: qsTr("Saving…")
                busy: buttons.busy
                onClicked: buttons.save()
            }

            BusyButton {
                objectName: "cancelButton"
                text: qsTr("Cancel")
                enabled: !buttons.busy
                onClicked: buttons.cancel()
            }
        }
    }

    // 顯示密碼（DS-51：Show／Hide），打完可以核對；表單關掉時又藏起來
    component RevealButton: BusyButton {
        text: section.passwordShown ? qsTr("Hide") : qsTr("Show")
        busyText: section.passwordShown ? qsTr("Show") : qsTr("Hide")   // 不會忙碌：只用來讓寬度取兩個字比較寬的，換字時輸入框不縮放
        iconSource: section.passwordShown ? "icons/eye-off_actionSecondaryText.svg" : "icons/eye_actionSecondaryText.svg"
        Accessible.name: section.passwordShown ? qsTr("Hide passwords") : qsTr("Show passwords")
        onClicked: section.passwordShown = !section.passwordShown
    }

    // ── 照片
    SettingsGroup {
        id: photoGroup
        objectName: "photoSection"
        title: qsTr("Profile Photo")

        RowLayout {
            width: photoGroup.width
            spacing: Theme.spaceL

            // 頭像：照片已經是圓形的 PNG（Python 做好），不需要遮罩
            Rectangle {
                objectName: "photoPreview"
                Layout.alignment: Qt.AlignTop
                implicitWidth: 88
                implicitHeight: 88
                radius: width / 2
                color: Theme.actionSecondaryBg
                border.color: Theme.tileBorder
                border.width: 1
                Accessible.role: Accessible.Graphic
                Accessible.name: section.account && section.account.hasPendingPhoto ? qsTr("New photo preview")
                               : avatarImage.visible ? qsTr("Your profile photo") : qsTr("No profile photo")

                Image {
                    anchors.centerIn: parent
                    visible: !avatarImage.visible
                    width: 44
                    height: 44
                    sourceSize: Qt.size(44, 44)
                    source: "icons/circle-user-round_iconNeutral.svg"
                    Accessible.ignored: true
                }

                Image {
                    id: avatarImage
                    objectName: "photoImage"
                    anchors.fill: parent
                    visible: section.avatarSource !== "" && status === Image.Ready
                    source: section.avatarSource
                    sourceSize: Qt.size(176, 176)
                    fillMode: Image.PreserveAspectFit
                    asynchronous: true
                    smooth: true
                    mipmap: true
                    Accessible.ignored: true
                }
            }

            ColumnLayout {
                Layout.fillWidth: true
                Layout.alignment: Qt.AlignVCenter
                spacing: Theme.spaceS

                Flow {
                    Layout.fillWidth: true
                    spacing: Theme.spaceS

                    BusyButton {
                        id: chooseButton
                        objectName: "choosePhotoButton"
                        text: section.account && section.account.hasPendingPhoto ? qsTr("Choose Another…") : qsTr("Choose…")
                        busyText: qsTr("Preparing…")
                        busy: section.account !== null && section.account.photoWork === "preparing"
                        enabled: section.profileReady && section.account.photoWork !== "saving"
                                 && section.account.photoWork !== "removing"
                        Accessible.description: qsTr("Choose a photo from this computer")
                        onClicked: photoDialog.open()
                    }

                    BusyButton {
                        objectName: "savePhotoButton"
                        visible: section.account !== null && section.account.hasPendingPhoto
                        variant: "primary"
                        text: qsTr("Save")
                        busyText: qsTr("Saving…")
                        busy: section.account !== null && section.account.photoWork === "saving"
                        Accessible.name: qsTr("Save photo")
                        onClicked: section.account.savePhoto()
                    }

                    BusyButton {
                        objectName: "discardPhotoButton"
                        visible: section.account !== null && section.account.hasPendingPhoto
                        text: qsTr("Cancel")
                        enabled: section.account !== null && section.account.photoWork !== "saving"
                        Accessible.name: qsTr("Cancel new photo")
                        onClicked: section.account.discardPhoto()
                    }

                    BusyButton {
                        objectName: "removePhotoButton"
                        visible: section.account !== null && !section.account.hasPendingPhoto
                                 && section.profile !== null && section.profile.hasPhoto
                        variant: "danger"
                        text: qsTr("Remove")
                        busyText: qsTr("Removing…")
                        busy: section.account !== null && section.account.photoWork === "removing"
                        enabled: section.account !== null && section.account.photoWork !== "preparing"
                        Accessible.name: qsTr("Remove photo")
                        onClicked: section.account.removePhoto()
                    }
                }

                FieldError {
                    objectName: "photoError"
                    Layout.fillWidth: true
                    visible: hasError
                    text: section.errors.photo || ""
                }

                Text {
                    objectName: "photoHint"
                    Layout.fillWidth: true
                    text: section.account && section.account.hasPendingPhoto
                          ? qsTr("This is a preview. Save it to use it as your photo.")
                          : qsTr("JPEG, PNG or HEIC, up to 5 MB. It's cropped to a square, and location data is removed.")
                    color: Theme.textMuted
                    font.pixelSize: Theme.fontSmall
                    lineHeight: 18
                    lineHeightMode: Text.FixedHeight
                    wrapMode: Text.Wrap
                }
            }
        }
    }

    SettingsDivider {}

    // ── 帳號資料
    SettingsGroup {
        id: detailsGroup
        objectName: "detailsSection"
        title: qsTr("Details")

        ColumnLayout {
            width: detailsGroup.width
            spacing: Theme.spaceS

            // 帳號資料沒有載入：顯示原因和 Retry，不顯示可以改的按鈕
            InlineError {
                objectName: "profileError"
                Layout.fillWidth: true
                visible: section.profile !== null && section.profile.state === "error"
                title: qsTr("Your account details didn't load")
                detail: section.profile ? section.profile.errorDetail : ""
                technicalDetail: section.profile ? section.profile.errorTechnical : ""
                failureCount: section.profile ? section.profile.failureCount : 0
                onRetry: section.profile.load()
            }

            // Display Name
            SummaryRow {
                objectName: "nameRow"
                label: qsTr("Display Name")
                value: section.profileReady ? section.profile.name : qsTr("Loading…")

                BusyButton {
                    id: editNameButton
                    objectName: "editNameButton"
                    visible: section.openForm !== "name"
                    enabled: section.profileReady
                    text: qsTr("Edit")
                    Accessible.name: qsTr("Edit display name")
                    onClicked: section.openEditor("name")
                }
            }

            ColumnLayout {
                objectName: "nameForm"
                Layout.fillWidth: true
                Layout.bottomMargin: Theme.spaceS
                visible: section.openForm === "name"
                spacing: Theme.spaceM

                LabeledField {
                    id: nameInput
                    objectName: "nameField"
                    label: qsTr("New Display Name")
                    maximumLength: 100      // tbl_account.account_name VARCHAR(100)
                    error: section.errors.name || ""
                    help: qsTr("Shown in the header, lists and reports. Reports you already have keep the old name.")
                    onEdited: section.account.clearError("name")
                    onSubmitted: section.saveName()
                    onCancelled: section.cancelForm()
                }

                FormButtons {
                    objectName: "nameButtons"
                    error: section.errors.nameForm || ""
                    busy: section.account !== null && section.account.nameBusy
                    onSave: section.saveName()
                    onCancel: section.cancelForm()
                }
            }

            // Username
            SummaryRow {
                objectName: "usernameRow"
                label: qsTr("Username")
                value: section.profileReady ? section.profile.username : qsTr("Loading…")

                BusyButton {
                    id: editUsernameButton
                    objectName: "editUsernameButton"
                    visible: section.openForm !== "username"
                    enabled: section.profileReady
                    text: qsTr("Edit Username")
                    onClicked: section.openEditor("username")
                }
            }

            ColumnLayout {
                objectName: "usernameForm"
                Layout.fillWidth: true
                Layout.bottomMargin: Theme.spaceS
                visible: section.openForm === "username"
                spacing: Theme.spaceM

                LabeledField {
                    id: usernameInput
                    objectName: "usernameField"
                    label: qsTr("New Username")
                    maximumLength: 64       // tbl_account.account_username VARCHAR(64)
                    error: section.errors.username || ""
                    help: qsTr("You'll log in with the new username next time. You stay logged in now.")
                    onEdited: section.account.clearError("username")
                    onSubmitted: section.saveUsername()
                    onCancelled: section.cancelForm()
                }

                LabeledField {
                    id: usernamePasswordInput
                    objectName: "usernamePasswordField"
                    label: qsTr("Current Password")
                    secret: true
                    error: section.errors.usernamePassword || ""
                    onEdited: section.account.clearError("usernamePassword")
                    onSubmitted: section.saveUsername()
                    onCancelled: section.cancelForm()

                    RevealButton { objectName: "showUsernamePasswordButton" }
                }

                FormButtons {
                    objectName: "usernameButtons"
                    error: section.errors.usernameForm || ""
                    busy: section.account !== null && section.account.usernameBusy
                    onSave: section.saveUsername()
                    onCancel: section.cancelForm()
                }
            }

            // Role：唯讀（自助頁不能改自己的等級）
            SummaryRow {
                objectName: "roleRow"
                label: qsTr("Role")
                value: section.profileReady ? section.roleText : qsTr("Loading…")
            }

            // Password：不讀回、不顯示（只有一排固定的點）
            SummaryRow {
                objectName: "passwordRow"
                label: qsTr("Password")
                value: "••••••••"
                valueName: qsTr("Hidden")

                BusyButton {
                    id: changePasswordButton
                    objectName: "changePasswordButton"
                    visible: section.openForm !== "password"
                    enabled: section.profileReady
                    text: qsTr("Change Password")
                    onClicked: section.openEditor("password")
                }
            }

            ColumnLayout {
                objectName: "passwordForm"
                Layout.fillWidth: true
                visible: section.openForm === "password"
                spacing: Theme.spaceM

                LabeledField {
                    id: currentPasswordInput
                    objectName: "currentPasswordField"
                    label: qsTr("Current Password")
                    secret: true
                    error: section.errors.currentPassword || ""
                    onEdited: section.account.clearError("currentPassword")
                    onSubmitted: section.savePassword()
                    onCancelled: section.cancelForm()

                    RevealButton { objectName: "showPasswordsButton" }
                }

                LabeledField {
                    id: newPasswordInput
                    objectName: "newPasswordField"
                    label: qsTr("New Password")
                    secret: true
                    error: section.errors.newPassword || ""
                    onEdited: section.account.clearError("newPassword")
                    onSubmitted: section.savePassword()
                    onCancelled: section.cancelForm()
                }

                LabeledField {
                    id: confirmPasswordInput
                    objectName: "confirmPasswordField"
                    label: qsTr("Confirm New Password")
                    secret: true
                    error: section.errors.confirmPassword || ""
                    help: qsTr("You'll log in with the new password next time. You stay logged in now.")
                    onEdited: section.account.clearError("confirmPassword")
                    onSubmitted: section.savePassword()
                    onCancelled: section.cancelForm()
                }

                FormButtons {
                    objectName: "passwordButtons"
                    error: section.errors.passwordForm || ""
                    busy: section.account !== null && section.account.passwordBusy
                    onSave: section.savePassword()
                    onCancel: section.cancelForm()
                }
            }
        }
    }
}
