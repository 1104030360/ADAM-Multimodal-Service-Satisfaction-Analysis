import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Controls.Material 2.15
import QtCharts 2.15
import QtQuick.Layouts 1.15

Page {
    id: root
    width: 1024
    height: 768
    title: "Ranking Overview"
    Material.theme: Material.Light
    Material.accent: Material.Green

    property var top5Customer: []
    property var bottom5Customer: []
    property var top5Server: []
    property var bottom5Server: []

    property real dailyServiceCount: 0
    property real weeklyServiceCount: 0
    property var dailyCustomerScores: ({'total': 0, 'text': 0, 'audio': 0, 'facial': 0})
    property var dailyServerScores: ({'total': 0, 'text': 0, 'audio': 0, 'facial': 0})
    property var weeklyCustomerScores: ({'total': 0, 'text': 0, 'audio': 0, 'facial': 0})
    property var weeklyServerScores: ({'total': 0, 'text': 0, 'audio': 0, 'facial': 0})

    property int currentTab: 0

    Connections {
        target: serviceScoreManager

        function onUpdateDailyServiceCount(value) {
            root.dailyServiceCount = value;
        }

        function onUpdateWeeklyServiceCount(value) {
            root.weeklyServiceCount = value;
        }

        function onUpdateDailyScores(customerScores, serverScores) {
            root.dailyCustomerScores = customerScores;
            root.dailyServerScores = serverScores;
        }

        function onUpdateWeeklyScores(customerScores, serverScores) {
            root.weeklyCustomerScores = customerScores;
            root.weeklyServerScores = serverScores;
        }
    }

    Connections {
        target: rankingModel

        function onUpdateRanking(top5Customer, bottom5Customer, top5Server, bottom5Server) {
            root.top5Customer = top5Customer;
            root.bottom5Customer = bottom5Customer;
            root.top5Server = top5Server;
            root.bottom5Server = bottom5Server;
        }
    }

    Connections {
        target: trendDataManager

        function onServerPointAdded(x, y) {
            serverLineSeries.append(x, y);
        }

        function onCustomerPointAdded(x, y) {
            customerLineSeries.append(x, y);
        }
    }

    TabBar {
        id: tabbar
        width: parent.width
        Layout.fillWidth: true
        Layout.preferredHeight: 40

        TabButton {
            text: "Daily Scores"
            onClicked: root.currentTab = 0
            Material.background: root.currentTab == 0 ? Material.accent : "#E7E7E7"
            font.pixelSize: 16
        }

        TabButton {
            text: "Weekly Scores"
            onClicked: root.currentTab = 1
            Material.background: root.currentTab == 1 ? Material.accent : "#E7E7E7"
            font.pixelSize: 16
        }

        TabButton {
            text: "Ranking"
            onClicked: root.currentTab = 2
            Material.background: root.currentTab == 2 ? Material.accent : "#E7E7E7"
            font.pixelSize: 16
        }
        
        TabButton {
            text: "Trends"
            onClicked: root.currentTab = 3
            Material.background: root.currentTab == 3 ? Material.accent : "#E7E7E7"
            font.pixelSize: 16
        }
    }

    // Daily Scores Page
    ColumnLayout {
        anchors.left: parent.left
        anchors.leftMargin: 270
        anchors.right: parent.right
        anchors.rightMargin: 200
        anchors.top: tabbar.bottom
        anchors.topMargin: 100
        spacing: 20
        width: parent.width - 400
        visible: root.currentTab == 0

        Text {
            text: "Daily service counts: " + root.dailyServiceCount
            font.pixelSize: 18
            horizontalAlignment: Text.AlignHCenter
            Layout.alignment: Qt.AlignCenter
        }

        // Customer Scores Section
        ColumnLayout {
            spacing: 20
            Layout.fillWidth: true
            Layout.alignment: Qt.AlignCenter

            Text {
                text: "Customer Scores"
                font.pixelSize: 22
                horizontalAlignment: Text.AlignHCenter
                Layout.alignment: Qt.AlignCenter
            }

            RowLayout {
                spacing: 30
                Layout.alignment: Qt.AlignCenter

                Repeater {
                    model: ["total", "text", "audio", "facial"]

                    ColumnLayout {
                        spacing: 10
                        Layout.alignment: Qt.AlignHCenter

                        Canvas {
                            width: 150
                            height: 150

                            onPaint: {
                                var ctx = getContext("2d");
                                var centerX = width / 2;
                                var centerY = height / 2;
                                var radius = width / 2.5;
                                var startAngle = -Math.PI / 2;
                                var scoreValue = root.dailyCustomerScores[modelData] / 100;
                                var endAngle = startAngle + (isNaN(scoreValue) ? 0 : scoreValue) * 2 * Math.PI;

                                var scoreColor;
                                if (scoreValue >= 0.85) {
                                    scoreColor = "green";
                                } else if (scoreValue >= 0.7) {
                                    scoreColor = "orange";
                                } else {
                                    scoreColor = "red";
                                }

                                ctx.strokeStyle = "#e0e0e0";
                                ctx.lineWidth = 14;
                                ctx.beginPath();
                                ctx.arc(centerX, centerY, radius, 0, 2 * Math.PI);
                                ctx.stroke();

                                ctx.strokeStyle = scoreColor;
                                ctx.beginPath();
                                ctx.arc(centerX, centerY, radius, startAngle, endAngle);
                                ctx.stroke();

                                ctx.fillStyle = "#000";
                                ctx.font = "bold 24px Arial";
                                ctx.textAlign = "center";
                                ctx.textBaseline = "middle";
                                ctx.fillText(Math.round(scoreValue * 100).toString(), centerX, centerY);
                            }
                        }

                        Text {
                            text: (modelData == "total") ? "OVERALL" : (modelData == "text") ? "TEXT" : (modelData == "audio") ? "AUDIO" : "FACIAL"
                            font.pixelSize: 16
                            Layout.alignment: Qt.AlignHCenter
                        }
                    }
                }
            }
        }

        // Server Scores Section
        ColumnLayout {
            spacing: 20
            Layout.fillWidth: true
            Layout.alignment: Qt.AlignCenter

            Text {
                text: "Server Scores"
                font.pixelSize: 22
                horizontalAlignment: Text.AlignHCenter
                Layout.alignment: Qt.AlignCenter
            }

            RowLayout {
                spacing: 30
                Layout.alignment: Qt.AlignCenter

                Repeater {
                    model: ["total", "text", "audio", "facial"]

                    ColumnLayout {
                        spacing: 10
                        Layout.alignment: Qt.AlignHCenter

                        Canvas {
                            width: 150
                            height: 150

                            onPaint: {
                                var ctx = getContext("2d");
                                var centerX = width / 2;
                                var centerY = height / 2;
                                var radius = width / 2.5;
                                var startAngle = -Math.PI / 2;
                                var scoreValue = root.dailyServerScores[modelData] / 100;
                                var endAngle = startAngle + (isNaN(scoreValue) ? 0 : scoreValue) * 2 * Math.PI;

                                var scoreColor;
                                if (scoreValue >= 0.85) {
                                    scoreColor = "green";
                                } else if (scoreValue >= 0.7) {
                                    scoreColor = "orange";
                                } else {
                                    scoreColor = "red";
                                }

                                ctx.strokeStyle = "#e0e0e0";
                                ctx.lineWidth = 14;
                                ctx.beginPath();
                                ctx.arc(centerX, centerY, radius, 0, 2 * Math.PI);
                                ctx.stroke();

                                ctx.strokeStyle = scoreColor;
                                ctx.beginPath();
                                ctx.arc(centerX, centerY, radius, startAngle, endAngle);
                                ctx.stroke();

                                ctx.fillStyle = "#000";
                                ctx.font = "bold 24px Arial";
                                ctx.textAlign = "center";
                                ctx.textBaseline = "middle";
                                ctx.fillText(Math.round(scoreValue * 100).toString(), centerX, centerY);
                            }
                        }

                        Text {
                            text: (modelData == "total") ? "OVERALL" : (modelData == "text") ? "TEXT" : (modelData == "audio") ? "AUDIO" : "FACIAL"
                            font.pixelSize: 16
                            Layout.alignment: Qt.AlignHCenter
                        }
                    }
                }
            }
        }
    }

    // Weekly Scores Page
    ColumnLayout {
        anchors.left: parent.left
        anchors.leftMargin: 270
        anchors.right: parent.right
        anchors.rightMargin: 200
        anchors.top: tabbar.bottom
        anchors.topMargin: 100
        spacing: 20
        width: parent.width - 400
        visible: root.currentTab == 1

        Text {
            text: "Weekly service counts: " + root.weeklyServiceCount
            font.pixelSize: 18
            horizontalAlignment: Text.AlignHCenter
            Layout.alignment: Qt.AlignCenter
        }

        ColumnLayout {
            spacing: 20
            Layout.fillWidth: true
            Layout.alignment: Qt.AlignCenter

            Text {
                text: "Customer Scores"
                font.pixelSize: 22
                horizontalAlignment: Text.AlignHCenter
                Layout.alignment: Qt.AlignCenter
            }

            RowLayout {
                spacing: 30
                Layout.alignment: Qt.AlignCenter

                Repeater {
                    model: ["total", "text", "audio", "facial"]

                    ColumnLayout {
                        spacing: 10
                        Layout.alignment: Qt.AlignHCenter

                        // Customer Circles for Weekly
                        Canvas {
                            width: 150
                            height: 150

                            onPaint: {
                                var ctx = getContext("2d");
                                var centerX = width / 2;
                                var centerY = height / 2;
                                var radius = width / 2.5;
                                var startAngle = -Math.PI / 2;
                                var scoreValue = root.weeklyCustomerScores[modelData] / 100;
                                var endAngle = startAngle + (isNaN(scoreValue) ? 0 : scoreValue) * 2 * Math.PI;

                                // Determine color based on score range
                                var scoreColor;
                                if (scoreValue >= 0.85) {
                                    scoreColor = "green"; 
                                } else if (scoreValue >= 0.7) {
                                    scoreColor = "orange"; 
                                } else {
                                    scoreColor = "red"; 
                                }

                                ctx.strokeStyle = "#e0e0e0";
                                ctx.lineWidth = 14;
                                ctx.beginPath();
                                ctx.arc(centerX, centerY, radius, 0, 2 * Math.PI);
                                ctx.stroke();

                                ctx.strokeStyle = scoreColor;
                                ctx.beginPath();
                                ctx.arc(centerX, centerY, radius, startAngle, endAngle);
                                ctx.stroke();

                                ctx.fillStyle = "#000";
                                ctx.font = "bold 24px Arial";
                                ctx.textAlign = "center";
                                ctx.textBaseline = "middle";
                                ctx.fillText(Math.round(scoreValue * 100).toString(), centerX, centerY);
                            }
                        }

                        Text {
                            text: (modelData == "total") ? "OVERALL" : (modelData == "text") ? "TEXT" : (modelData == "audio") ? "AUDIO" : "FACIAL"
                            font.pixelSize: 16
                            Layout.alignment: Qt.AlignHCenter
                        }
                    }
                }
            }
        }

        ColumnLayout {
            spacing: 20
            Layout.fillWidth: true
            Layout.alignment: Qt.AlignCenter

            Text {
                text: "Server Scores"
                font.pixelSize: 22
                horizontalAlignment: Text.AlignHCenter
                Layout.alignment: Qt.AlignCenter
            }

            RowLayout {
                spacing: 30
                Layout.alignment: Qt.AlignCenter

                Repeater {
                    model: ["total", "text", "audio", "facial"]

                    ColumnLayout {
                        spacing: 10
                        Layout.alignment: Qt.AlignCenter

                        // Server Circles for Weekly
                        Canvas {
                            width: 150
                            height: 150

                            onPaint: {
                                var ctx = getContext("2d");
                                var centerX = width / 2;
                                var centerY = height / 2;
                                var radius = width / 2.5;
                                var startAngle = -Math.PI / 2;
                                var scoreValue = root.weeklyServerScores[modelData] / 100;
                                var endAngle = startAngle + (isNaN(scoreValue) ? 0 : scoreValue) * 2 * Math.PI;

                                // Determine color based on score range
                                var scoreColor;
                                if (scoreValue >= 0.85) {
                                    scoreColor = "green"; 
                                } else if (scoreValue >= 0.7) {
                                    scoreColor = "orange"; 
                                } else {
                                    scoreColor = "red"; 
                                }

                                ctx.strokeStyle = "#e0e0e0";
                                ctx.lineWidth = 14;
                                ctx.beginPath();
                                ctx.arc(centerX, centerY, radius, 0, 2 * Math.PI);
                                ctx.stroke();

                                ctx.strokeStyle = scoreColor;
                                ctx.beginPath();
                                ctx.arc(centerX, centerY, radius, startAngle, endAngle);
                                ctx.stroke();

                                ctx.fillStyle = "#000";
                                ctx.font = "bold 24px Arial";
                                ctx.textAlign = "center";
                                ctx.textBaseline = "middle";
                                ctx.fillText(Math.round(scoreValue * 100).toString(), centerX, centerY);
                            }
                        }

                        Text {
                            text: (modelData == "total") ? "OVERALL" : (modelData == "text") ? "TEXT" : (modelData == "audio") ? "AUDIO" : "FACIAL"
                            font.pixelSize: 16
                            Layout.alignment: Qt.AlignHCenter
                        }
                    }
                }
            }
        }
    }

    // Ranking Page
    GridLayout {
        visible: root.currentTab == 2
        anchors.fill: parent
        anchors.margins: 40
        rows: 2
        columns: 2
        rowSpacing: 30
        columnSpacing: 30

        // Top 5 Customers Section
        Rectangle {
            color: "#E8F5E9"
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.alignment: Qt.AlignVCenter | Qt.AlignRight
            Layout.margins: 20

            ColumnLayout {
                anchors.fill: parent

                Rectangle {
                    width: parent.width
                    height: 50
                    color: "#C8E6C9"
                    border.color: "#388E3C"
                    border.width: 2
                    radius: 8
                    Layout.alignment: Qt.AlignHCenter

                    Text {
                        anchors.centerIn: parent
                        text: "Top 5 Customers"
                        font.pixelSize: 22
                        font.bold: true
                        color: "#388E3C"
                    }
                }

                Repeater {
                    model: 5
                    delegate: RowLayout {
                        spacing: 10
                        Text {
                            text: (index + 1) + "."
                            font.pixelSize: 16
                            color: "#388E3C"
                        }
                        Text {
                            text: index < root.top5Customer.length ? root.top5Customer[index].name + " (" + root.top5Customer[index].score + ")" : "N/A"
                            font.pixelSize: 16
                            color: "#388E3C"
                        }
                    }
                }
            }
        }

        // Bottom 5 Customers Section
        Rectangle {
            color: "#FFF3E0"
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.alignment: Qt.AlignVCenter | Qt.AlignLeft
            Layout.margins: 20

            ColumnLayout {
                anchors.fill: parent

                Rectangle {
                    width: parent.width
                    height: 50
                    color: "#FFE0B2"
                    border.color: "#FB8C00"
                    border.width: 2
                    radius: 8
                    Layout.alignment: Qt.AlignHCenter

                    Text {
                        anchors.centerIn: parent
                        text: "Bottom 5 Customers"
                        font.pixelSize: 22
                        font.bold: true
                        color: "#FB8C00"
                    }
                }

                Repeater {
                    model: 5
                    delegate: RowLayout {
                        spacing: 10
                        Text {
                            text: (index + 1) + "."
                            font.pixelSize: 16
                            color: "#FB8C00"
                        }
                        Text {
                            text: index < root.bottom5Customer.length ? root.bottom5Customer[index].name + " (" + root.bottom5Customer[index].score + ")" : "N/A"
                            font.pixelSize: 16
                            color: "#FB8C00"
                        }
                    }
                }
            }
        }

        // Top 5 Servers Section
        Rectangle {
            color: "#E3F2FD"
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.alignment: Qt.AlignVCenter | Qt.AlignRight
            Layout.margins: 20

            ColumnLayout {
                anchors.fill: parent

                Rectangle {
                    width: parent.width
                    height: 50
                    color: "#BBDEFB"
                    border.color: "#1E88E5"
                    border.width: 2
                    radius: 8
                    Layout.alignment: Qt.AlignHCenter

                    Text {
                        anchors.centerIn: parent
                        text: "Top 5 Servers"
                        font.pixelSize: 22
                        font.bold: true
                        color: "#1E88E5"
                    }
                }

                Repeater {
                    model: 5
                    delegate: RowLayout {
                        spacing: 10
                        Text {
                            text: (index + 1) + "."
                            font.pixelSize: 16
                            color: "#1E88E5"
                        }
                        Text {
                            text: index < root.top5Server.length ? root.top5Server[index].name + " (" + root.top5Server[index].score + ")" : "N/A"
                            font.pixelSize: 16
                            color: "#1E88E5"
                        }
                    }
                }
            }
        }

        // Bottom 5 Servers Section
        Rectangle {
            color: "#FCE4EC"
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.alignment: Qt.AlignVCenter | Qt.AlignLeft
            Layout.margins: 20

            ColumnLayout {
                anchors.fill: parent

                Rectangle {
                    width: parent.width
                    height: 50
                    color: "#F8BBD0"
                    border.color: "#EC407A"
                    border.width: 2
                    radius: 8
                    Layout.alignment: Qt.AlignHCenter

                    Text {
                        anchors.centerIn: parent
                        text: "Bottom 5 Servers"
                        font.pixelSize: 22
                        font.bold: true
                        color: "#EC407A"
                    }
                }

                Repeater {
                    model: 5
                    delegate: RowLayout {
                        spacing: 10
                        Text {
                            text: (index + 1) + "."
                            font.pixelSize: 16
                            color: "#EC407A"
                        }
                        Text {
                            text: index < root.bottom5Server.length ? root.bottom5Server[index].name + " (" + root.bottom5Server[index].score + ")" : "N/A"
                            font.pixelSize: 16
                            color: "#EC407A"
                        }
                    }
                }
            }
        }
    }
    // Trends Page
    ColumnLayout {
        anchors.left: parent.left
        anchors.leftMargin: 50
        anchors.right: parent.right
        anchors.rightMargin: 50
        anchors.top: tabbar.bottom
        anchors.bottom: parent.bottom
        spacing: 20
        visible: root.currentTab == 3

        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 10

            // Month and Year selection
            RowLayout {
                spacing: 20
                Layout.alignment: Qt.AlignCenter  // Centering the RowLayout

                ComboBox {
                    id: monthSelector
                    model: ["January", "February", "March", "April", "May", "June",
                            "July", "August", "September", "October", "November", "December"]
                    currentIndex: new Date().getMonth()  // 預設本月；getMonth() 從 0 開始
                    Layout.alignment: Qt.AlignCenter
                    onActivated: root.reloadTrends()  // 只在使用者改選時觸發
                }

                ComboBox {
                    id: yearSelector
                    // 今年和前 4 年；跨年之後新的年份會自動出現
                    model: {
                        var thisYear = new Date().getFullYear();
                        var years = [];
                        for (var y = thisYear - 4; y <= thisYear; y++)
                            years.push(y);
                        return years;
                    }
                    currentIndex: 4  // 今年
                    Layout.alignment: Qt.AlignCenter
                    onActivated: root.reloadTrends()
                }
            }

            // Checkboxes to select trends to display
            RowLayout {
                spacing: 20
                Layout.alignment: Qt.AlignCenter  // Centering the RowLayout

                CheckBox {
                    id: showServerTrend
                    text: "Show Server Trend"
                    checked: true
                    Layout.alignment: Qt.AlignCenter
                    font.pixelSize: 14
                }

                CheckBox {
                    id: showCustomerTrend
                    text: "Show Customer Trend"
                    checked: true
                    Layout.alignment: Qt.AlignCenter
                    font.pixelSize: 14
                }
            }

            ChartView {
                id: chartView
                Layout.fillWidth: true
                Layout.fillHeight: true
                antialiasing: true

                ValueAxis {
                    id: valueAxisX
                    min: 0
                    max: 31  // Assuming the maximum days in a month
                    tickCount: 32
                    labelFormat: "%.0f"
                    titleText: "Day"
                }

                ValueAxis {
                    id: valueAxisY
                    min: 0
                    max: 100
                    tickCount: 11
                    labelFormat: "%.0f"
                    titleText: "Score"
                }

                LineSeries {
                    id: serverLineSeries
                    name: "Server Score Trends"
                    axisX: valueAxisX
                    axisY: valueAxisY
                    visible: showServerTrend.checked
                }

                LineSeries {
                    id: customerLineSeries
                    name: "Customer Score Trends"
                    axisX: valueAxisX
                    axisY: valueAxisY
                    visible: showCustomerTrend.checked
                    color: "red"
                }
            }
        }
    }
    // 依照目前選的月份、年份重畫趨勢圖
    function reloadTrends() {
        serverLineSeries.clear();
        customerLineSeries.clear();
        trendDataManager.load_trends_for_month(monthSelector.currentIndex + 1, Number(yearSelector.currentText));
    }

    Component.onCompleted: {
        serviceScoreManager.load_scores();
        rankingModel.load_ranking();
        reloadTrends();
    }
}
