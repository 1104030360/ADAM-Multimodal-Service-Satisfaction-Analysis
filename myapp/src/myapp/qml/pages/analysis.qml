import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Controls.Material 2.15
import QtQuick.Layouts 1.15

Page {
    id: analysisPage
    width: 1024
    height: 768
    title: "Service Management"
    Material.theme: Material.Light
    Material.accent: Material.Green

    property var selectedTab: "completed"

    // Button Bar for navigation
    RowLayout {
        id: buttonBar
        Layout.fillWidth: true
        spacing: 10
        anchors.top: parent.top
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.topMargin: 10
        anchors.bottomMargin: 20  // 增加這裡的margin來增加與下方list的距離

        Rectangle {
            width: 150
            height: 50
            radius: 25
            color: selectedTab === "completed" ? "#e53935" : "#e0e0e0"
            border.color: "#e53935"
            border.width: 2

            Text {
                text: "已完成"
                anchors.centerIn: parent
                font.pixelSize: 16
                color: selectedTab === "completed" ? "#ffffff" : "#000000"
            }

            MouseArea {
                anchors.fill: parent
                onClicked: {
                    selectedTab = "completed";
                    completedSection.visible = true;
                    inprogressSection.visible = false;
                    uncompletedSection.visible = false;
                }
            }
        }

        Rectangle {
            width: 150
            height: 50
            radius: 25
            color: selectedTab === "inprogress" ? "#e53935" : "#e0e0e0"
            border.color: "#e53935"
            border.width: 2

            Text {
                text: "進行中"
                anchors.centerIn: parent
                font.pixelSize: 16
                color: selectedTab === "inprogress" ? "#ffffff" : "#000000"
            }

            MouseArea {
                anchors.fill: parent
                onClicked: {
                    selectedTab = "inprogress";
                    completedSection.visible = false;
                    inprogressSection.visible = true;
                    uncompletedSection.visible = false;
                }
            }
        }

        Rectangle {
            width: 150
            height: 50
            radius: 25
            color: selectedTab === "uncompleted" ? "#e53935" : "#e0e0e0"
            border.color: "#e53935"
            border.width: 2

            Text {
                text: "未完成"
                anchors.centerIn: parent
                font.pixelSize: 16
                color: selectedTab === "uncompleted" ? "#ffffff" : "#000000"
            }

            MouseArea {
                anchors.fill: parent
                onClicked: {
                    selectedTab = "uncompleted";
                    completedSection.visible = false;
                    inprogressSection.visible = false;
                    uncompletedSection.visible = true;
                }
            }
        }
    }

    // Completed services section
    Column {
        id: completedSection
        visible: selectedTab === "completed"
        anchors.top: buttonBar.bottom
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.leftMargin: 0
        anchors.rightMargin: 0
        anchors.topMargin: 24
        anchors.bottomMargin: -24

        Rectangle {
            width: parent.width
            height: 40
            color: "#FFFFFF"
            border.color: "#D1D1D1"
            border.width: 1

            Text {
                text: "Service ID"
                color: "#2C3E50"
                font.pixelSize: 16
                anchors.left: parent.left
                anchors.verticalCenter: parent.verticalCenter
                width: 200
                horizontalAlignment: Text.AlignHCenter
            }
            Text {
                text: "Start Time"
                color: "#2C3E50"
                font.pixelSize: 16
                anchors.left: parent.left
                anchors.leftMargin: 200
                anchors.verticalCenter: parent.verticalCenter
                width: 250
                horizontalAlignment: Text.AlignHCenter
            }
            Text {
                text: "Staff / Customer"
                color: "#2C3E50"
                font.pixelSize: 16
                anchors.left: parent.left
                anchors.leftMargin: 450
                anchors.verticalCenter: parent.verticalCenter
                width: 250
                horizontalAlignment: Text.AlignHCenter
            }
            Text {
                text: "Action"
                color: "#2C3E50"
                font.pixelSize: 16
                anchors.left: parent.left
                anchors.leftMargin: 700
                anchors.verticalCenter: parent.verticalCenter
                width: 200
                horizontalAlignment: Text.AlignHCenter
            }
        }

        ScrollView {
            width: parent.width
            height: parent.height - 40
            ListView {
                id: completedList
                width: parent.width
                height: parent.height
                model: serviceModel.completedModel
                delegate: Rectangle {
                    width: parent.width
                    height: 50
                    color: index % 2 == 0 ? "#F2F6FA" : "#FFFFFF"
                    border.color: "#D1D1D1"
                    border.width: 1

                    Text {
                        text: modelData.service_id
                        anchors.left: parent.left
                        anchors.verticalCenter: parent.verticalCenter
                        width: 200
                        horizontalAlignment: Text.AlignHCenter
                    }
                    Text {
                        text: modelData.service_start
                        anchors.left: parent.left
                        anchors.leftMargin: 200
                        anchors.verticalCenter: parent.verticalCenter
                        width: 250
                        horizontalAlignment: Text.AlignHCenter
                    }
                    Text {
                        text: modelData.server_total && modelData.customer_total ? modelData.server_total + " / " + modelData.customer_total : "N/A"
                        anchors.left: parent.left
                        anchors.leftMargin: 450
                        anchors.verticalCenter: parent.verticalCenter
                        width: 250
                        horizontalAlignment: Text.AlignHCenter
                    }
                    Button {
                        text: "PDF"
                        anchors.left: parent.left
                        anchors.leftMargin: 750
                        anchors.verticalCenter: parent.verticalCenter
                        width: 100
                        Material.background: Material.accent
                        Material.foreground: "#FFFFFF"
                    }
                }
            }
        }
    }

    // In-progress services section
    Column {
        id: inprogressSection
        visible: selectedTab === "inprogress"
        anchors.top: buttonBar.bottom
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.leftMargin: 0
        anchors.rightMargin: 0
        anchors.topMargin: 24
        anchors.bottomMargin: -24

        Rectangle {
            width: parent.width
            height: 40
            color: "#FFFFFF"
            border.color: "#D1D1D1"
            border.width: 1

            Text {
                text: "Service ID"
                color: "#2C3E50"
                font.pixelSize: 16
                anchors.left: parent.left
                anchors.verticalCenter: parent.verticalCenter
                width: 300
                horizontalAlignment: Text.AlignHCenter
            }
            Text {
                text: "Start Time"
                color: "#2C3E50"
                font.pixelSize: 16
                anchors.left: parent.left
                anchors.leftMargin: 300
                anchors.verticalCenter: parent.verticalCenter
                width: 300
                horizontalAlignment: Text.AlignHCenter
            }
            Text {
                text: "End Time / Status"
                color: "#2C3E50"
                font.pixelSize: 16
                anchors.left: parent.left
                anchors.leftMargin: 600
                anchors.verticalCenter: parent.verticalCenter
                width: 200
                horizontalAlignment: Text.AlignHCenter
            }
        }

        ScrollView {
            width: parent.width
            height: parent.height - 40
            ListView {
                id: inprogressList
                width: parent.width
                height: parent.height
                model: serviceModel.inprogressModel
                delegate: Rectangle {
                    width: parent.width
                    height: 50
                    color: index % 2 == 0 ? "#F2F6FA" : "#FFFFFF"
                    border.color: "#D1D1D1"
                    border.width: 1

                    Text {
                        text: modelData.service_id
                        anchors.left: parent.left
                        anchors.verticalCenter: parent.verticalCenter
                        width: 300
                        horizontalAlignment: Text.AlignHCenter
                    }
                    Text {
                        text: modelData.service_start
                        anchors.left: parent.left
                        anchors.leftMargin: 300
                        anchors.verticalCenter: parent.verticalCenter
                        width: 300
                        horizontalAlignment: Text.AlignHCenter
                    }
                    Text {
                        text: "進行中..."
                        anchors.left: parent.left
                        anchors.leftMargin: 600
                        anchors.verticalCenter: parent.verticalCenter
                        width: 200
                        horizontalAlignment: Text.AlignHCenter
                    }
                }
            }
        }
    }

    // Uncompleted services section
    Column {
        id: uncompletedSection
        visible: selectedTab === "uncompleted"
        anchors.top: buttonBar.bottom
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.leftMargin: 0
        anchors.rightMargin: 0
        anchors.topMargin: 24
        anchors.bottomMargin: -24

        Rectangle {
            width: parent.width
            height: 40
            color: "#FFFFFF"
            border.color: "#D1D1D1"
            border.width: 1

            Text {
                text: "Service ID"
                color: "#2C3E50"
                font.pixelSize: 16
                anchors.left: parent.left
                anchors.verticalCenter: parent.verticalCenter
                width: 300
                horizontalAlignment: Text.AlignHCenter
            }
            Text {
                text: "Start Time"
                color: "#2C3E50"
                font.pixelSize: 16
                anchors.left: parent.left
                anchors.leftMargin: 300
                anchors.verticalCenter: parent.verticalCenter
                width: 300
                horizontalAlignment: Text.AlignHCenter
            }
            Text {
                text: "End Time"
                color: "#2C3E50"
                font.pixelSize: 16
                anchors.left: parent.left
                anchors.leftMargin: 600
                anchors.verticalCenter: parent.verticalCenter
                width: 200
                horizontalAlignment: Text.AlignHCenter
            }
            Text {
                text: "Action"
                color: "#2C3E50"
                font.pixelSize: 16
                anchors.left: parent.left
                anchors.leftMargin: 800
                anchors.verticalCenter: parent.verticalCenter
                width: 200
                horizontalAlignment: Text.AlignHCenter
            }
        }

        ScrollView {
            width: parent.width
            height: parent.height - 40
            ListView {
                id: uncompletedList
                width: parent.width
                height: parent.height
                model: serviceModel.uncompletedModel
                delegate: Rectangle {
                    width: parent.width
                    height: 50
                    color: index % 2 == 0 ? "#F2F6FA" : "#FFFFFF"
                    border.color: "#D1D1D1"
                    border.width: 1

                    Text {
                        text: modelData.service_id
                        anchors.left: parent.left
                        anchors.verticalCenter: parent.verticalCenter
                        width: 300
                        horizontalAlignment: Text.AlignHCenter
                    }
                    Text {
                        text: modelData.service_start
                        anchors.left: parent.left
                        anchors.leftMargin: 300
                        anchors.verticalCenter: parent.verticalCenter
                        width: 300
                        horizontalAlignment: Text.AlignHCenter
                    }
                    Text {
                        text: modelData.service_end
                        anchors.left: parent.left
                        anchors.leftMargin: 600
                        anchors.verticalCenter: parent.verticalCenter
                        width: 200
                        horizontalAlignment: Text.AlignHCenter
                    }
                    Button {
                        text: "分析"
                        anchors.left: parent.left
                        anchors.leftMargin: 850
                        anchors.verticalCenter: parent.verticalCenter
                        width: 100
                        Material.background: Material.accent
                        Material.foreground: "#FFFFFF"
                        onClicked: {
                            if (!serviceModel.analyzeService(modelData.service_id))
                                analyzeFailedDialog.open()
                        }
                    }
                }
            }
        }
    }

    Dialog {
        id: analyzeFailedDialog
        anchors.centerIn: parent
        modal: true
        title: "送出分析失敗"
        standardButtons: Dialog.Ok

        Label {
            width: 360
            wrapMode: Text.WordWrap
            text: "連不到分析服務。請確認網路，以及 .env 的 ANALYZE_API_URL，再按一次「分析」。"
        }
    }

    // 分析服務做完會把狀態改成 3；每 15 秒重新查一次，不必重開程式就看得到
    Timer {
        interval: 15000
        running: true
        repeat: true
        onTriggered: serverHome.onLoadAnalysis()
    }

    // Connections to update models when data changes
    Connections {
        target: serviceModel

        function onUncompletedModelChanged() {
            uncompletedList.model = null;
            uncompletedList.model = serviceModel.uncompletedModel;
        }

        function onInprogressModelChanged() {
            inprogressList.model = null;
            inprogressList.model = serviceModel.inprogressModel;
        }

        function onCompletedModelChanged() {
            completedList.model = null;
            completedList.model = serviceModel.completedModel;
        }
    }
}
