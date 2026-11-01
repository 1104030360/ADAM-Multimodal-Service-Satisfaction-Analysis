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
    title: "Server Home"
    visibility: "FullScreen"

    // 動態設定寫進 Theme 單例（ENG-18）；程式結束時 context property 會先被刪掉，要先判斷是否為 null
    Binding { target: Theme; property: "motionMode"; value: uiPrefs ? uiPrefs.motionMode : 0 }
    Binding { target: Theme; property: "animationScale"; value: uiPrefs ? uiPrefs.animationScale : 1 }

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
                text: serverHome ? serverHome.accountName : ""  // 在 db lane 載入，到了就更新
                // 名字還沒載入時也先留一行，下面的按鈕不會跳（Column 會跳過寬或高是 0 的項目，所以寬度也要固定）
                width: parent.width
                height: 30
                elide: Text.ElideRight
                verticalAlignment: Text.AlignVCenter
                font.pixelSize: 24
                color: "#ffffff"
                horizontalAlignment: Text.AlignHCenter
                anchors.horizontalCenter: parent.horizontalCenter
                font.weight: Font.Bold
                font.bold: true
                font.family: "Arial"
            }

            // Home Button
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
                        console.log("Home clicked")
                        mainContentLoader.source = "../../qml/pages/server_home_button.qml"
                        serverHome.onLoadHome()  // 在背景重新載入；舊的分數留著、變淡
                    }
                }
            }

            // Record Button
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
                        id: recordImage
                        source: "../../images/icons/record.png"
                        width: 30
                        height: 30
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.left: parent.left
                    }

                    Text {
                        text: "Record"
                        font.pixelSize: 20
                        color: "#ffffff"
                        font.bold: true
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.left: recordImage.right
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
                        console.log("Record clicked")
                        mainContentLoader.source = "../../qml/pages/record_widget.qml"
                    }
                }
            }

            // Analysis Button
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
                        id: analysisImage
                        source: "../../images/icons/analysis.png" 
                        width: 30
                        height: 30
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.left: parent.left
                    }

                    Text {
                        text: "Analysis"
                        font.pixelSize: 20
                        color: "#ffffff"
                        font.bold: true
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.left: analysisImage.right
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
                        console.log("Analysis clicked")
                        mainContentLoader.source = "../../qml/pages/analysis.qml"  // 先換頁
                        serverHome.onLoadAnalysis()  // 清單在背景重新整理（1 個往返）
                    }
                }
            }

            //service
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
                    id: rectangle6
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
                    id: mouseArea6
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    onClicked: {
                        console.log("Service clicked")
                        mainContentLoader.source = "../../qml/pages/server_service_page.qml"
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
                        when: mouseArea7.containsMouse
                        PropertyChanges {
                            target: buttonBackground7
                            color: "#555555"
                        }
                    },
                    State {
                        name: "pressed"
                        when: mouseArea7.pressed
                        PropertyChanges {
                            target: buttonBackground7
                            color: "#666666"
                        }
                    }
                ]

                transitions: [
                    Transition {
                        from: "*"
                        to: "*"
                        ColorAnimation {
                            target: buttonBackground7
                            property: "color"
                            duration: 200
                        }
                    }
                ]

                Rectangle {
                    id: buttonBackground7
                    anchors.fill: parent
                    color: "#444444"
                    radius: 10
                }

                Rectangle {
                    id: rectangle7
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
                    id: mouseArea7
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    onClicked: {
                        console.log("Reports Page clicked")
                        mainContentLoader.source = "../../qml/pages/server_reports_page.qml"
                    }
                }
            }
            // Setting Button
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
                    id: mouseArea3
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    onClicked: {
                        console.log("Setting clicked")
                        mainContentLoader.source = "../../qml/pages/server_setting_button.qml"
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
                    id: mouseArea4
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    onClicked: {
                        console.log("Exit clicked")
                        serverHome.clearDir()
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
        source: "../../qml/pages/server_home_button.qml" // 初始顯示的內容
        onLoaded: {
            if (item && typeof item.setWindow === "function") {
                item.setWindow(window)
            }
        }
    }

    // 通知堆疊：內容區右下角，距邊 24（DS-21）。延伸到底部的清單用 toastHost.occupiedHeight 當 bottomMargin
    ToastHost {
        id: toastHost
        objectName: "toastHost"
        center: notifier
        x: window.width - width - Theme.spaceXL
        y: window.height - height - Theme.spaceXL
    }
}
