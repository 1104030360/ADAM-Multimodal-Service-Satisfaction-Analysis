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
    title: qsTr("Welcome to ADAM")
    color: Theme.chromeWindow // 和開場畫面的底色一樣，淡入時不會先閃白

    // 動態設定寫進 Theme 單例（ENG-18）：開場圖要照使用者的 Reduce Motion，測試的 animationScale 0 也要生效。
    // 外殼載入後也寫同一組值；context property 在程式結束時會先被刪掉，要先判斷是否為 null
    Binding { target: Theme; property: "motionMode"; value: uiPrefs ? uiPrefs.motionMode : 0 }
    Binding { target: Theme; property: "animationScale"; value: uiPrefs ? uiPrefs.animationScale : 1 }

    // 開場圖的總長（D3、DS-72／M-10）：淡入 durSplash＋停 durSplash＋換成表單 durBase = 950 ms，最多 1 s。
    // 要改長短就改 Theme.durSplash
    readonly property int splashTotal: 2 * Theme.durSplash + Theme.durBase
    property bool splashShown: false    // 這次啟動有沒有出現開場圖（減少動態時整段不出現）

    // 開場圖結束（時間到、點一下、按任意鍵）：換成登入表單。已經換過（例如測試直接換頁）就不再換，表單不會被重建
    function showLoginForm() {
        if (stackView.currentItem && stackView.currentItem.objectName === "splash")
            stackView.replace(loginComponent)
    }

    // 登出之後（N1，mainWindow.showLogin()）：同一個視窗再出現，不重播開場圖。換一份新的表單：帳號、密碼、錯誤都是空的，
    // 表單出現時（StackView.onActivated）焦點在帳號欄。被換掉的表單不再處理登入結果（它的 Connections 看 StackView.status）
    function showAgain() {
        stackView.replace(loginComponent, {}, StackView.Immediate)
        login_window.show()
        login_window.raise()
        login_window.requestActivate()
    }

    Connections {
        target: mainWindow
        function onLoginRequested() { login_window.showAgain() }
    }

    // 第一個畫面在這裡才決定：這時 Binding 已經把 uiPrefs 寫進 Theme。
    // 減少動態（或 animationScale 0）時 durSplash 是 0，開場圖整段不出現，直接是登入表單（DS-43）
    Component.onCompleted: {
        splashShown = Theme.durSplash > 0
        stackView.push(splashShown ? splashComponent : loginComponent, {}, StackView.Immediate)
    }

    StackView {
        id: stackView
        anchors.fill: parent
        focus: true     // 開場圖拿得到按鍵（按任意鍵跳過）

        // 開場圖換成登入表單：表單在上面淡入 durBase，開場圖留在下面不動，不滑動（DS-42）。
        // 時間是 0 時不套用：Animator 的時間是 0 會停在起點（第二波的教訓）
        replaceEnter: Transition {
            enabled: Theme.durBase > 0
            OpacityAnimator { id: formFadeIn; from: 0; to: 1; duration: Theme.durBase; easing.type: Theme.easeEnter }
        }
        replaceExit: Transition {
            enabled: Theme.durBase > 0
            PauseAnimation { duration: Theme.durBase }
        }

        // StackView 會自己把頁面調整成和它一樣大，頁面不要再綁 parent 的大小或設 anchors
        Component {
            id: splashComponent

            // 開場圖：OpacityAnimator 在 render thread 上淡入（ENG-19），GUI 執行緒在忙也不會卡；
            // 以前是 2 s 的 NumberAnimation＋2.5 s 的 Timer，要等 2.5 s 才能登入（UX-04）
            Item {
                id: splash
                objectName: "splash"
                focus: true

                Image {
                    id: logo
                    objectName: "splashLogo"
                    anchors.fill: parent
                    source: "../../images/logo.jpg"
                    fillMode: Image.PreserveAspectCrop
                    opacity: 0
                    Accessible.ignored: true
                }

                OpacityAnimator {
                    id: fadeIn
                    objectName: "splashFade"
                    target: logo
                    from: 0
                    to: 1
                    duration: Theme.durSplash
                    easing.type: Theme.easeEnter
                    running: true
                }

                Timer {
                    id: holdTimer
                    objectName: "splashTimer"
                    interval: 2 * Theme.durSplash   // 淡入之後再停一樣久
                    running: true
                    onTriggered: login_window.showLoginForm()
                }

                // 點一下或按任意鍵就跳過（D3）
                MouseArea {
                    anchors.fill: parent
                    onPressed: login_window.showLoginForm()
                }
                Keys.onPressed: (event) => {
                    event.accepted = true
                    login_window.showLoginForm()
                }
            }
        }

        Component {
            id: loginComponent

            // 登入表單（DS-31、UX-01～03）：按 Log In 或在任一欄按 Enter 送出，在背景驗證（mainWindow.logIn）。
            // 驗證中按鈕忙碌、欄位唯讀；結果由 mainWindow 的三個 signal 帶回來：
            // 成功就開主畫面；帳號或密碼不正確、連不上資料庫，各有自己的說明和焦點位置。
            // FocusScope：StackView 換頁時會把焦點交給整個表單（setFocus），焦點落在設了 focus 的帳號欄。
            // 沒有轉場（減少動態、測試）時 StackView 在 onActivated 之後才交焦點，表單要是普通的 Item，焦點會停在表單本身
            FocusScope {
                id: loginForm
                objectName: "loginForm"

                function submit() {
                    if (loginButton.busy)
                        return
                    error_message.text = "" // 上一次的錯誤清掉
                    mainWindow.logIn(username_textfield.text, password_textfield.text)
                }

                StackView.onActivated: username_textfield.forceActiveFocus() // 一進來就能打帳號

                Connections {
                    target: mainWindow
                    // 只有在 StackView 裡（正在顯示、或正要顯示）的表單處理結果：被換掉的表單在真的刪掉之前還活著，
                    // 不能讓它再開一次主畫面（例如測試把一開始的表單再換成新的一份）
                    enabled: loginForm.StackView.status === StackView.Active
                             || loginForm.StackView.status === StackView.Activating

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
                    focus: true     // 表單（FocusScope）拿到焦點時就在這裡：一進來就能打帳號（UX-03）
                    anchors.horizontalCenter: parent.horizontalCenter
                    y: logo_text.y + logo_text.height + 60
                    width: 300
                    height: 36
                    leftPadding: Theme.spaceM // 文字不要貼到圓角
                    placeholderText: qsTr("Username")
                    font.pixelSize: Theme.fontBodyLarge
                    color: Theme.chromeText
                    placeholderTextColor: Theme.chromeTextMuted
                    selectionColor: Theme.chromeSelection
                    selectedTextColor: Theme.textOnDark
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
                    selectionColor: Theme.chromeSelection
                    selectedTextColor: Theme.textOnDark
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
                            // 用 id 取 text：直接寫 text 會用到 textChanged 注入的參數（Qt 6 已經不建議，會印警告）
                            onTextChanged: {
                                if (error_message.text !== "")
                                    Accessible.announce(error_message.text, Accessible.Assertive) // 不搖晃，用唸的
                            }
                        }
                    }
                }
            }
        }
    }
}
