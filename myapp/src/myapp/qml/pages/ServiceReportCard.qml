pragma ComponentBehavior: Bound
import QtQuick
// 自訂了按鈕的 background、contentItem，要用 Basic 樣式（T13）
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "../components"

// 管理員明細頁的報表卡（new-feature N4，T37）：這筆服務的每次服務報表。PLAN 7.3 原本寫在分析頁的 PDF 按鈕，
// 但新版介面的管理員端沒有分析頁，所以放在服務明細（只有分析完成的服務才有這張卡）。
// - 狀態跟著 reportsModel.detailReport（報表清單裡這筆服務最新的一列）：沒有、產生中、好了還沒發佈、已發佈、失敗。
// - 動作：Generate Report、Open（換到報表頁打開這一份）、Publish、Generate Again（已發佈的再產生一份新的，
//   發佈之前服務人員仍看到上一份，R5）、Retry。
// - 打開時讀一次清單、有產生中的就輪詢（pageOpened("detail")），離開時停（pageClosed）。
Card {
    id: card

    property var vm: null
    property string serviceId: ""

    readonly property var report: vm ? vm.detailReport : ({})
    readonly property bool hasReport: report.reportId !== undefined
    readonly property string listState: vm ? vm.listState : "idle"
    readonly property bool known: listState === "ready" || listState === "empty"
    readonly property string status: hasReport ? report.status : ""
    readonly property bool generatingHere: vm !== null && vm.generatingKeys.indexOf("service|" + serviceId) >= 0
    readonly property bool publishing: hasReport && vm !== null && vm.publishing.indexOf(report.reportId) >= 0
    readonly property string note: {
        if (!known)
            return listState === "error" ? qsTr("Couldn't check for a report.") : qsTr("Checking for a report…")
        if (!hasReport)
            return qsTr("No report for this service yet.")
        if (status === "generating")
            return qsTr("The analysis service is making this report.")
        if (status === "failed")
            return report.failureReason !== "" ? report.failureReason : qsTr("This report couldn't be generated.")
        if (report.published)
            return qsTr("%1. Staff can see it.").arg(report.publishedText)
        return qsTr("Staff can't see this report until you publish it.")
    }

    topPadding: 18
    bottomPadding: 18
    Accessible.role: Accessible.Grouping
    Accessible.name: qsTr("Report for this service")

    Component.onCompleted: {
        if (!vm)
            return
        vm.watchService(serviceId)
        vm.pageOpened("detail")
    }
    Component.onDestruction: {
        if (vm)
            vm.pageClosed("detail")
    }
    onServiceIdChanged: {
        if (vm)
            vm.watchService(serviceId)
    }

    RowLayout {
        width: parent ? parent.width : 0
        spacing: Theme.spaceM

        Image {
            Layout.alignment: Qt.AlignVCenter
            Layout.preferredWidth: Theme.iconM
            Layout.preferredHeight: Theme.iconM
            sourceSize: Qt.size(Theme.iconM, Theme.iconM)
            source: "../components/icons/file-text_iconNeutral.svg"
            Accessible.ignored: true
        }

        Column {
            Layout.fillWidth: true
            Layout.alignment: Qt.AlignVCenter
            spacing: 4

            Row {
                spacing: Theme.spaceS

                Text {
                    anchors.verticalCenter: parent.verticalCenter
                    text: qsTr("Report")
                    color: Theme.textTitle
                    font.pixelSize: Theme.fontBody
                    font.weight: Font.Bold
                    Accessible.role: Accessible.Heading
                    Accessible.name: text
                }
                StatusChip {
                    objectName: "detailReportChip"
                    anchors.verticalCenter: parent.verticalCenter
                    visible: card.hasReport
                    reportState: card.hasReport ? card.report.chipState : ""
                }
            }

            Text {
                objectName: "detailReportNote"
                width: parent.width
                text: card.note
                color: card.status === "failed" || card.listState === "error" ? Theme.errorOnDark : Theme.textMuted
                font.pixelSize: Theme.fontCaption
                wrapMode: Text.Wrap
            }
        }

        BusyButton {
            objectName: "detailReportRetryList"
            visible: card.listState === "error"
            text: qsTr("Retry")
            onClicked: {
                if (card.vm)
                    card.vm.refresh()
            }
        }

        BusyButton {
            objectName: "detailOpenReport"
            visible: card.known && card.status === "ready"
            text: qsTr("Open")
            Accessible.name: qsTr("Open the report for %1").arg(card.serviceId)
            onClicked: window.openReport(card.report.reportId)
        }

        BusyButton {
            objectName: "detailPublishReport"
            visible: card.known && card.hasReport && card.report.canPublish
            variant: "primary"
            text: qsTr("Publish")
            busyText: qsTr("Publishing…")
            busy: card.publishing
            Accessible.name: qsTr("Publish the report for %1").arg(card.serviceId)
            onClicked: {
                if (!busy && card.vm)
                    card.vm.publish(card.report.reportId)
            }
        }

        // 沒有報表：產生；已發佈：再產生一份（新的一列，發佈之前服務人員仍看到這一份）；失敗：Retry
        BusyButton {
            objectName: "detailGenerateReport"
            visible: card.known && (!card.hasReport || card.status === "failed" || card.report.published
                                    || card.status === "generating")
            variant: !card.hasReport || card.status === "failed" ? "primary" : "secondary"
            text: !card.hasReport ? qsTr("Generate Report") : card.status === "failed" ? qsTr("Retry")
                                                                                       : qsTr("Generate Again")
            busyText: qsTr("Generating…")
            busy: card.vm !== null && (card.vm.submitting || card.generatingHere)
            Accessible.name: text + ", " + card.serviceId
            onClicked: {
                if (busy || !card.vm)
                    return
                if (card.status === "failed")
                    card.vm.retry(card.report.reportId)
                else
                    card.vm.generateForService(card.serviceId)
            }
        }
    }
}
