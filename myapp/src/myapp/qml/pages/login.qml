import QtQuick 2.15
// 這頁的輸入框自訂了 background；macOS 預設的原生樣式不支援自訂，所以改用 Basic 樣式
import QtQuick.Controls.Basic 2.15
import QtQuick.Layouts 1.15
import QtQuick.Window 2.15
import "../components"

Window {
    id: login_window
    visible: true
    width: 1024
    height: 768
    title: "Welcome to ADAM"
    color: Theme.chromeWindow // 和開場畫面的底色一樣，淡入時不會先閃白

    StackView {
        id: stackView
        anchors.fill: parent

        // StackView 會自己把頁面調整成和它一樣大，頁面不要再綁 parent 的大小或設 anchors
        initialItem: Item {
            id: splash

            Image {
                id: logo
                source: "../../images/logo.jpg"
                anchors.fill: parent
                fillMode: Image.PreserveAspectCrop
                opacity: 0 // 初始透明度为 0

                Behavior on opacity {
                    NumberAnimation {
                        duration: 2000 // 淡入时间为 2000 毫秒
                    }
                }

                Component.onCompleted: {
                    console.log("Logo image loaded")
                    logo.opacity = 1 // 触发淡入动画
                }
            }

            Timer {
                interval: 2500 // 比动画稍长，确保动画完全展示
                running: true
                repeat: false
                onTriggered: {
                    console.log("Timer triggered")
                    if (stackView.currentItem === splash) // 已經換過（例如測試直接換頁）就不再換，表單不會被重建
                        stackView.replace(loginComponent) // 动画结束后切换页面
                }
            }
        }

        Component {
            id: loginComponent

            // 登入表單（DS-31、UX-01～03）：按 Log In 或在任一欄按 Enter 送出，在背景驗證（mainWindow.logIn）。
            // 驗證中按鈕忙碌、欄位唯讀；結果由 mainWindow 的三個 signal 帶回來：
            // 成功就開主畫面；帳號或密碼不正確、連不上資料庫，各有自己的說明和焦點位置。
            Rectangle {
                id: loginForm
                color: "transparent" // 背景透明以显示背景图片

                function submit() {
                    if (loginButton.busy)
                        return
                    error_message.text = "" // 上一次的錯誤清掉
                    mainWindow.logIn(username_textfield.text, password_textfield.text)
                }

                StackView.onActivated: username_textfield.forceActiveFocus() // 一進來就能打帳號

                Connections {
                    target: mainWindow

                    function onLoginSucceeded(accountId, accountLevel) {
                        if (accountLevel === "1") {
                            mainWindow.launchServerHome(accountId)
                        } else {
                            mainWindow.launchAdminHome(accountId)
                        }
                        login_window.close()
                    }

                    function onLoginRejected() {
                        error_message.text = qsTr("Incorrect username or password. Try again.")
                        password_textfield.forceActiveFocus() // 可以直接重打密碼
                        password_textfield.selectAll()
                    }

                    function onLoginFailed(reason) {
                        // 密碼沒錯，不清掉；焦點留在按鈕上，按 Enter 就重試
                        error_message.text = reason === "offline"
                            ? qsTr("Can't connect to the database. Check your network and try again.")
                            : qsTr("Can't log in because of a database problem. Contact your administrator.")
                        loginButton.forceActiveFocus(Qt.TabFocusReason)
                    }
                }

                Image {
                    id: login_image
                    anchors.fill: parent
                    source: "../../images/ADAM_login.jpg"
                    fillMode: Image.PreserveAspectCrop
                }

                Rectangle {
                    id: login_rectangle
                    anchors.horizontalCenter: parent.horizontalCenter
                    anchors.verticalCenter: parent.verticalCenter
                    width: parent.width * 0.4
                    height: parent.height * 0.6
                    opacity: Theme.chromePanelOpacity
                    color: Theme.chromePanel
                    radius: Theme.radiusL
                    border.color: Theme.chromeBorder
                    border.width: 1
                }

                Text {
                    id: logo_text
                    anchors.horizontalCenter: parent.horizontalCenter
                    // letterSpacing 也會加在最後一個字母後面，字會往左偏 16 / 2 = 8 px，這裡移回中間
                    anchors.horizontalCenterOffset: Theme.wordmarkSpacing / 2
                    y: login_rectangle.y + 50
                    width: 203
                    height: 60
                    color: Theme.chromeTitle
                    text: qsTr("ADAM")
                    font.pixelSize: Theme.fontDisplay
                    horizontalAlignment: Text.AlignHCenter
                    font.family: "Helvetica Neue"
                    font.weight: Font.Light
                    font.letterSpacing: Theme.wordmarkSpacing // 和開場畫面的細字、寬字距一致
                    Accessible.ignored: true
                }

                TextField {
                    id: username_textfield
                    anchors.horizontalCenter: parent.horizontalCenter
                    y: logo_text.y + logo_text.height + 60
                    width: 300
                    height: 36
                    leftPadding: Theme.spaceM // 文字不要貼到圓角
                    placeholderText: qsTr("Username")
                    font.pixelSize: Theme.fontBodyLarge
                    color: Theme.chromeText
                    placeholderTextColor: Theme.chromeTextMuted
                    selectionColor: "#4B515A"
                    selectedTextColor: "#FFFFFF"
                    readOnly: loginButton.busy // 驗證中唯讀但不停用，字不會變灰（DS-31）
                    Accessible.name: qsTr("Username") // placeholder 不能當標籤用
                    onAccepted: loginForm.submit()
                    background: Rectangle {
                        color: Theme.chromeField
                        radius: height / 2
                        border.color: username_textfield.activeFocus ? Theme.chromeFocus : Theme.chromeFieldBorder
                        border.width: 1
                    }
                }

                TextField {
                    id: password_textfield
                    anchors.horizontalCenter: parent.horizontalCenter
                    y: username_textfield.y + username_textfield.height + 40
                    width: 300
                    height: 36
                    leftPadding: Theme.spaceM
                    placeholderText: qsTr("Password")
                    font.pixelSize: Theme.fontBodyLarge
                    echoMode: TextInput.Password // 隐藏输入的密码
                    color: Theme.chromeText
                    placeholderTextColor: Theme.chromeTextMuted
                    selectionColor: "#4B515A"
                    selectedTextColor: "#FFFFFF"
                    readOnly: loginButton.busy
                    Accessible.name: qsTr("Password")
                    onAccepted: loginForm.submit()
                    background: Rectangle {
                        color: Theme.chromeField
                        radius: height / 2
                        border.color: password_textfield.activeFocus ? Theme.chromeFocus : Theme.chromeFieldBorder
                        border.width: 1
                    }
                }

                // 按下就擋住第二次點擊；300 ms 後才換成「Logging in…」和旋轉圖示（DS-24）
                BusyButton {
                    id: loginButton
                    objectName: "loginButton"
                    anchors.horizontalCenter: parent.horizontalCenter
                    y: password_textfield.y + password_textfield.height + 50
                    width: 200
                    large: true
                    variant: "chrome"
                    font.pixelSize: Theme.fontBody
                    text: qsTr("Log In")
                    busyText: qsTr("Logging in…")
                    busy: mainWindow ? mainWindow.loggingIn : false
                    onClicked: loginForm.submit()
                    Keys.onReturnPressed: loginForm.submit()
                    Keys.onEnterPressed: loginForm.submit()
                }

                // 錯誤說明：按鈕下方，左右各留 40 px，太長就排成兩行，圖示對齊第一行（DS-31）
                Item {
                    id: error_row
                    x: login_rectangle.x + 40
                    y: loginButton.y + loginButton.height + 20
                    width: login_rectangle.width - 80
                    height: error_message.height
                    visible: error_message.text !== ""
                    Accessible.role: Accessible.AlertMessage
                    Accessible.name: error_message.text

                    Row {
                        anchors.horizontalCenter: parent.horizontalCenter
                        spacing: 6

                        Image {
                            y: (20 - height) / 2
                            width: 14
                            height: 14
                            sourceSize: Qt.size(14, 14)
                            source: "../components/icons/circle-alert_errorOnDark.svg"
                            Accessible.ignored: true
                        }

                        Text {
                            id: error_message
                            objectName: "loginError"
                            width: Math.min(implicitWidth, error_row.width - 20)
                            color: Theme.errorOnDark
                            font.pixelSize: Theme.fontCaption
                            lineHeight: 20
                            lineHeightMode: Text.FixedHeight
                            wrapMode: Text.Wrap
                            onTextChanged: {
                                if (text !== "")
                                    Accessible.announce(text, Accessible.Assertive) // 不搖晃，用唸的
                            }
                        }
                    }
                }
            }
        }
    }
}
