import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import QtQuick.Window 2.15
import "../components"

Window {
    id: window
    visible: true
    width: 1920
    height: 1080
    title: "Admin Home"
    visibility: "FullScreen"




    Rectangle {
        id: sidebar
        y: 0
        width: 288
        height: parent.height
        color: "#333333"
        anchors.left: parent.left
        anchors.leftMargin: 0

        Column {
            id: sidebarContent
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.bottom: parent.bottom
            spacing: 20
            anchors.margins: 20
            anchors.leftMargin: 0
            anchors.rightMargin: 0
            anchors.topMargin: 50
            anchors.bottomMargin: 588

            Image {
                id: avatar
                source: "../../images/icons/user.png" 
                width: 100
                height: 100
                anchors.horizontalCenter: parent.horizontalCenter
                fillMode: Image.PreserveAspectFit
            }

            Text {
                id: username
                // 帳號名稱在背景載入（ENG-06）：載入前是空的，不在 binding 裡同步查資料庫
                text: adminHome ? adminHome.profile.name : ""
                font.pixelSize: 24
                color: "#ffffff"
                horizontalAlignment: Text.AlignHCenter
                anchors.horizontalCenter: parent.horizontalCenter
                font.weight: Font.Bold
                font.bold: true
                font.family: "Arial"
            }

            // Home Page Button
            Rectangle {
                width: parent.width
                height: 50
                color: "#444444"
                radius: 10

                states: [
                    State {
                        name: "hovered"
                        when: mouseArea.containsMouse
                        PropertyChanges {
                            target: buttonBackground
                            color: "#555555"
                        }
                    },
                    State {
                        name: "pressed"
                        when: mouseArea.pressed
                        PropertyChanges {
                            target: buttonBackground
                            color: "#666666"
                        }
                    }
                ]

                transitions: [
                    Transition {
                        from: "*"
                        to: "*"
                        ColorAnimation {
                            target: buttonBackground
                            property: "color"
                            duration: 200
                        }
                    }
                ]

                Rectangle {
                    id: buttonBackground
                    anchors.fill: parent
                    color: "#444444"
                    radius: 10
                }

                Rectangle {
                    id: rectangle
                    width: parent.width
                    height: parent.height
                    color: "transparent"
                    anchors {
                        left: parent.left
                        right: parent.right
                        top: parent.top
                        bottom: parent.bottom
                        margins: 10
                    }

                    Image {
                        id: homeImage
                        width: 30
                        height: 30
                        source: "../../images/icons/home.png"
                        anchors.left: parent.left
                        anchors.leftMargin: 0
                    }

                    Text {
                        text: "Home"
                        font.pixelSize: 20
                        color: "#ffffff"
                        font.bold: true
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.left: homeImage.right
                        anchors.right: parent.right
                        anchors.leftMargin: 10
                        anchors.rightMargin: 110
                    }
                }

                MouseArea {
                    id: mouseArea
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    onClicked: {
                        console.log("Home Page clicked")
                        mainContentLoader.source = "../../qml/pages/admin_home_page.qml"
                    }
                }
            }

            // Add Page Button
            Rectangle {
                width: parent.width
                height: 50
                color: "#444444"
                radius: 10

                states: [
                    State {
                        name: "hovered"
                        when: mouseArea2.containsMouse
                        PropertyChanges {
                            target: buttonBackground2
                            color: "#555555"
                        }
                    },
                    State {
                        name: "pressed"
                        when: mouseArea2.pressed
                        PropertyChanges {
                            target: buttonBackground2
                            color: "#666666"
                        }
                    }
                ]

                transitions: [
                    Transition {
                        from: "*"
                        to: "*"
                        ColorAnimation {
                            target: buttonBackground2
                            property: "color"
                            duration: 200
                        }
                    }
                ]

                Rectangle {
                    id: buttonBackground2
                    anchors.fill: parent
                    color: "#444444"
                    radius: 10
                }

                Rectangle {
                    id: rectangle1
                    width: parent.width
                    height: parent.height
                    color: "transparent"
                    anchors {
                        left: parent.left
                        right: parent.right
                        top: parent.top
                        bottom: parent.bottom
                        margins: 10
                    }

                    Image {
                        id: addImage
                        source: "../../images/icons/add.png"
                        width: 30
                        height: 30
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.left: parent.left
                    }

                    Text {
                        text: "Add"
                        font.pixelSize: 20
                        color: "#ffffff"
                        font.bold: true
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.left: addImage.right
                        anchors.right: parent.right
                        anchors.leftMargin: 10
                        anchors.rightMargin: 110
                    }
                }

                MouseArea {
                    id: mouseArea2
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    onClicked: {
                        console.log("Add Page clicked")
                        // 不再順便載入首頁的資料：新增帳號頁用不到（UX-32）
                        mainContentLoader.source = "../../qml/pages/admin_add_page.qml"
                    }
                }
            }

            // Service Page Button
            Rectangle {
                width: parent.width
                height: 50
                color: "#444444"
                radius: 10

                states: [
                    State {
                        name: "hovered"
                        when: mouseArea3.containsMouse
                        PropertyChanges {
                            target: buttonBackground3
                            color: "#555555"
                        }
                    },
                    State {
                        name: "pressed"
                        when: mouseArea3.pressed
                        PropertyChanges {
                            target: buttonBackground3
                            color: "#666666"
                        }
                    }
                ]

                transitions: [
                    Transition {
                        from: "*"
                        to: "*"
                        ColorAnimation {
                            target: buttonBackground3
                            property: "color"
                            duration: 200
                        }
                    }
                ]

                Rectangle {
                    id: buttonBackground3
                    anchors.fill: parent
                    color: "#444444"
                    radius: 10
                }

                Rectangle {
                    id: rectangle2
                    width: parent.width
                    height: parent.height
                    color: "transparent"
                    anchors {
                        left: parent.left
                        right: parent.right
                        top: parent.top
                        bottom: parent.bottom
                        margins: 10
                    }

                    Image {
                        id: serviceImage
                        source: "../../images/icons/service.png"
                        width: 30
                        height: 30
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.left: parent.left
                    }

                    Text {
                        text: "Service"
                        font.pixelSize: 20
                        color: "#ffffff"
                        font.bold: true
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.left: serviceImage.right
                        anchors.right: parent.right
                        anchors.leftMargin: 10
                        anchors.rightMargin: 110
                    }
                }

                MouseArea {
                    id: mouseArea3
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    onClicked: {
                        console.log("Service Page clicked")
                        mainContentLoader.source = "../../qml/pages/admin_service_page.qml"
                    }
                }
            }

            // Reports Page Button
            Rectangle {
                width: parent.width
                height: 50
                color: "#444444"
                radius: 10

                states: [
                    State {
                        name: "hovered"
                        when: mouseArea4.containsMouse
                        PropertyChanges {
                            target: buttonBackground4
                            color: "#555555"
                        }
                    },
                    State {
                        name: "pressed"
                        when: mouseArea4.pressed
                        PropertyChanges {
                            target: buttonBackground4
                            color: "#666666"
                        }
                    }
                ]

                transitions: [
                    Transition {
                        from: "*"
                        to: "*"
                        ColorAnimation {
                            target: buttonBackground4
                            property: "color"
                            duration: 200
                        }
                    }
                ]

                Rectangle {
                    id: buttonBackground4
                    anchors.fill: parent
                    color: "#444444"
                    radius: 10
                }

                Rectangle {
                    id: rectangle3
                    width: parent.width
                    height: parent.height
                    color: "transparent"
                    anchors {
                        left: parent.left
                        right: parent.right
                        top: parent.top
                        bottom: parent.bottom
                        margins: 10
                    }

                    Image {
                        id: reportsImage
                        source: "../../images/icons/reports.png"
                        width: 30
                        height: 30
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.left: parent.left
                    }

                    Text {
                        text: "Reports"
                        font.pixelSize: 20
                        color: "#ffffff"
                        font.bold: true
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.left: reportsImage.right
                        anchors.right: parent.right
                        anchors.leftMargin: 10
                        anchors.rightMargin: 110
                    }
                }

                MouseArea {
                    id: mouseArea4
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    onClicked: {
                        console.log("Reports Page clicked")
                        mainContentLoader.source = "../../qml/pages/admin_reports_page.qml"
                    }
                }
            }

            // Setting Page Button
            Rectangle {
                width: parent.width
                height: 50
                color: "#444444"
                radius: 10

                states: [
                    State {
                        name: "hovered"
                        when: mouseArea5.containsMouse
                        PropertyChanges {
                            target: buttonBackground5
                            color: "#555555"
                        }
                    },
                    State {
                        name: "pressed"
                        when: mouseArea5.pressed
                        PropertyChanges {
                            target: buttonBackground5
                            color: "#666666"
                        }
                    }
                ]

                transitions: [
                    Transition {
                        from: "*"
                        to: "*"
                        ColorAnimation {
                            target: buttonBackground5
                            property: "color"
                            duration: 200
                        }
                    }
                ]

                Rectangle {
                    id: buttonBackground5
                    anchors.fill: parent
                    color: "#444444"
                    radius: 10
                }

                Rectangle {
                    id: rectangle4
                    width: parent.width
                    height: parent.height
                    color: "transparent"
                    anchors {
                        left: parent.left
                        right: parent.right
                        top: parent.top
                        bottom: parent.bottom
                        margins: 10
                    }

                    Image {
                        id: settingImage
                        source: "../../images/icons/setting.png"
                        width: 30
                        height: 30
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.left: parent.left
                    }

                    Text {
                        text: "Setting"
                        font.pixelSize: 20
                        color: "#ffffff"
                        font.bold: true
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.left: settingImage.right
                        anchors.right: parent.right
                        anchors.leftMargin: 10
                        anchors.rightMargin: 110
                    }
                }

                MouseArea {
                    id: mouseArea5
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    onClicked: {
                        console.log("Setting Page clicked")
                        mainContentLoader.source = "../../qml/pages/admin_setting_page.qml"
                    }
                }
            }

            // Exit Button
            Rectangle {
                width: parent.width
                height: 50
                color: "#444444"
                radius: 10

                states: [
                    State {
                        name: "hovered"
                        when: mouseArea6.containsMouse
                        PropertyChanges {
                            target: buttonBackground6
                            color: "#555555"
                        }
                    },
                    State {
                        name: "pressed"
                        when: mouseArea6.pressed
                        PropertyChanges {
                            target: buttonBackground6
                            color: "#666666"
                        }
                    }
                ]

                transitions: [
                    Transition {
                        from: "*"
                        to: "*"
                        ColorAnimation {
                            target: buttonBackground6
                            property: "color"
                            duration: 200
                        }
                    }
                ]

                Rectangle {
                    id: buttonBackground6
                    anchors.fill: parent
                    color: "#444444"
                    radius: 10
                }

                Rectangle {
                    id: rectangle5
                    width: parent.width
                    height: parent.height
                    color: "transparent"
                    anchors {
                        left: parent.left
                        right: parent.right
                        top: parent.top
                        bottom: parent.bottom
                        margins: 10
                    }

                    Image {
                        id: exitImage
                        source: "../../images/icons/exit.png"
                        width: 30
                        height: 30
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.left: parent.left
                    }

                    Text {
                        text: "Exit"
                        font.pixelSize: 20
                        color: "#ffffff"
                        font.bold: true
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.left: exitImage.right
                        anchors.right: parent.right
                        anchors.leftMargin: 10
                        anchors.rightMargin: 130
                    }
                }

                MouseArea {
                    id: mouseArea6
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    onClicked: {
                        console.log("Exit clicked")
                        adminHome.clearDir()
                        Qt.quit() // 退出應用程式
                    }
                }
            }
        }
    }

    Loader {
        id: mainContentLoader
        anchors.left: sidebar.right
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        anchors.leftMargin: 0
        source: "../../qml/pages/admin_home_page.qml" // 初始顯示的內容
        onLoaded: {
            if (item && typeof item.setWindow === "function") {
                item.setWindow(window)
            }
        }
    }

    // 減少動態效果（ENG-18、DS-43）：設定頁的選項、系統設定、測試的 animationScale 都經由 Theme 生效。
    // uiPrefs 由 MainWindow（或 launch()）註冊；是 null 時不寫，Theme 用自己的預設值
    Binding {
        target: Theme
        property: "motionMode"
        when: !!uiPrefs
        value: uiPrefs ? uiPrefs.motionMode : 0
    }

    Binding {
        target: Theme
        property: "animationScale"
        when: !!uiPrefs
        value: uiPrefs ? uiPrefs.animationScale : 1
    }

    // 通知堆疊（DS-21）：內容區的右下角、距邊 24，蓋在頁面和對話框上面。
    // 延伸到內容區底部的清單要設 bottomMargin: toastHost.occupiedHeight，最後一列才捲得出來
    ToastHost {
        id: toastHost
        center: notifier
        x: window.width - width - Theme.spaceXL
        y: window.height - height - Theme.spaceXL
    }
}
