import QtQuick 2.15
// 這頁的輸入框自訂了 background；macOS 預設的原生樣式不支援自訂，所以改用 Basic 樣式
import QtQuick.Controls.Basic 2.15
import QtQuick.Layouts 1.15
import QtQuick.Window 2.15

Window {
    id: login_window
    visible: true
    width: 1024
    height: 768
    title: "Welcome to ADAM"
    color: "#0B0C0E" // 和開場畫面的底色一樣，淡入時不會先閃白

    StackView {
        id: stackView
        anchors.fill: parent

        // StackView 會自己把頁面調整成和它一樣大，頁面不要再綁 parent 的大小或設 anchors
        initialItem: Item {
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
                    stackView.replace(loginComponent) // 动画结束后切换页面
                }
            }
        }

        Component {
            id: loginComponent
            Rectangle {
                color: "transparent" // 背景透明以显示背景图片

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
                    opacity: 0.78
                    color: "#0E1013"
                    radius: 20
                    border.color: "#596069"
                    border.width: 1
                }

                Text {
                    id: logo_text
                    anchors.horizontalCenter: parent.horizontalCenter
                    // letterSpacing 也會加在最後一個字母後面，字會往左偏 16 / 2 = 8 px，這裡移回中間
                    anchors.horizontalCenterOffset: 8
                    y: login_rectangle.y + 50
                    width: 203
                    height: 60
                    color: "#F3F4F6"
                    text: qsTr("ADAM")
                    font.pixelSize: 60
                    horizontalAlignment: Text.AlignHCenter
                    font.family: "Helvetica Neue"
                    font.weight: Font.Light
                    font.letterSpacing: 16 // 和開場畫面的細字、寬字距一致
                }

                TextField {
                    id: username_textfield
                    anchors.horizontalCenter: parent.horizontalCenter
                    y: logo_text.y + logo_text.height + 60
                    width: 300
                    height: 36
                    leftPadding: 16 // 文字不要貼到圓角
                    placeholderText: qsTr("Username")
                    font.pixelSize: 18
                    color: "#ECEEF1"
                    placeholderTextColor: "#8E959F"
                    selectionColor: "#4B515A"
                    selectedTextColor: "#FFFFFF"
                    background: Rectangle {
                        color: "#17191D"
                        radius: 18
                        border.color: username_textfield.activeFocus ? "#A9B0BA" : "#606770"
                        border.width: 1
                    }
                }

                TextField {
                    id: password_textfield
                    anchors.horizontalCenter: parent.horizontalCenter
                    y: username_textfield.y + username_textfield.height + 40
                    width: 300
                    height: 36
                    leftPadding: 16
                    placeholderText: qsTr("Password")
                    font.pixelSize: 18
                    echoMode: TextInput.Password // 隐藏输入的密码
                    color: "#ECEEF1"
                    placeholderTextColor: "#8E959F"
                    selectionColor: "#4B515A"
                    selectedTextColor: "#FFFFFF"
                    background: Rectangle {
                        color: "#17191D"
                        radius: 18
                        border.color: password_textfield.activeFocus ? "#A9B0BA" : "#606770"
                        border.width: 1
                    }
                }

                Rectangle {
                    id: button
                    anchors.horizontalCenter: parent.horizontalCenter
                    y: password_textfield.y + password_textfield.height + 50
                    width: 200
                    height: 50
                    // 顏色只由這個綁定決定；在 onPressed 裡寫 button.color = ... 會把綁定拆掉，hover 的顏色就會卡住
                    color: mouseArea.pressed ? "#C3C8CF" : (mouseArea.containsMouse ? "#FFFFFF" : "#E6E8EB")
                    radius: 10

                    MouseArea {
                        id: mouseArea
                        anchors.fill: parent
                        cursorShape: Qt.PointingHandCursor
                        hoverEnabled: true

                        onClicked: {
                            console.log("Login button clicked")
                            var result = mainWindow.checkLogin(username_textfield.text, password_textfield.text)
                            if (result.success) {
                                console.log("Login successful, account level: " + result.account_level + ", account ID: " + result.account_id)
                                if (result.account_level === "1") {
                                    mainWindow.launchServerHome(result.account_id)
                                } else {
                                    mainWindow.launchAdminHome(result.account_id)
                                }
                                login_window.close()
                            } else {
                                console.log("Login failed")
                                error_message.text = "Login failed, please try again."
                                error_message.visible = true
                            }
                        }
                    }

                    Text {
                        text: "Login"
                        anchors.centerIn: parent
                        color: "#0E1013"
                        font.pixelSize: 16
                        font.weight: Font.DemiBold
                    }
                }

                Text {
                    id: error_message
                    anchors.horizontalCenter: parent.horizontalCenter
                    y: button.y + button.height + 20
                    color: "#FF9A90"
                    visible: false
                }
            }
        }
    }
}
