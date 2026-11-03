pragma ComponentBehavior: Bound
import QtQuick
// 自訂了 Details 按鈕的 background、contentItem，要用 Basic 樣式（T13）
import QtQuick.Controls.Basic

// 設定頁的 Help 區塊（new-feature PLAN 13.4 的 S3、13.6 的 S5；T40）。三組：
// - About：版本與基本環境，直接顯示（helpCenter.about）。讀不到的欄位由 Python 寫成 Unavailable。
// - Connection：Check Connection 按需檢查 Database、Cloud Storage、Analysis Service（helpCenter.connection）。
//   每一項：狀態標籤（Not Checked／Checking…／Available／Failed）、一兩句結果與下一步、檢查的時間，
//   技術細節收在 Details。一項 Available 只代表那一項有回應，下面的說明也這樣寫。
//   Copy Diagnostic Summary 只複製白名單的欄位（helpCenter.copySummary()），不傳到任何地方。
// - Storage：下載快取的大小、剩餘空間、Clear Download Cache（helpCenter.storage）。清之前跳確認，說明清什麼、不清什麼；
//   有下載或報表正在用快取時按鈕停用，下面寫原因。
// helpCenter 是兩端的 launch() 註冊的 context property；元件測試可以把 center 換成假的。
SettingsSection {
    id: help

    property var window: null       // 外殼的視窗（頁面的 setWindow）；這一區不用，和其他區塊的介面一樣
    property var center: typeof helpCenter !== "undefined" ? helpCenter : null

    readonly property var about: center ? center.about : ({})
    readonly property var connection: center ? center.connection : null
    readonly property var storage: center ? center.storage : null
    readonly property var checks: connection ? connection.items : []
    readonly property int labelWidth: 200      // About、Storage 左邊標籤欄的寬度
    property var announced: []                  // 唸過的內容，測試和除錯用

    function announce(text) {
        announced = announced.concat([text])
        Accessible.announce(text, Accessible.Polite)
    }

    title: qsTr("Help")

    // 打開設定頁時算一次快取大小（io lane，不在 GUI 執行緒讀檔案）
    Component.onCompleted: {
        if (storage)
            storage.refresh()
    }

    Connections {
        target: help.connection
        function onFinished() {
            help.announce(qsTr("Connection check finished"))
        }
    }

    Connections {
        target: help.storage
        function onChanged() {
            if (help.storage.resultText !== "" && help.storage.resultText !== internal.lastResult)
                help.announce(help.storage.resultText)
            internal.lastResult = help.storage.resultText
        }
    }

    QtObject {
        id: internal
        property string lastResult: ""
    }

    // ── 一列「標籤：值」（About、Storage）
    component InfoRow: Row {
        id: info

        property string label: ""
        property string value: ""
        property int labelWidth: 200

        spacing: Theme.spaceM
        Accessible.role: Accessible.StaticText
        Accessible.name: qsTr("%1: %2").arg(label).arg(value)

        Text {
            width: info.labelWidth
            text: info.label
            color: Theme.textMuted
            font.pixelSize: Theme.fontCaption
            elide: Text.ElideRight
            Accessible.ignored: true
        }

        Text {
            objectName: "infoValue"
            width: Math.max(0, info.width - info.labelWidth - info.spacing)
            text: info.value
            textFormat: Text.PlainText
            color: Theme.textPrimary
            font.pixelSize: Theme.fontCaption
            font.weight: Font.DemiBold
            wrapMode: Text.Wrap
            Accessible.ignored: true
        }
    }

    // ── 檢查結果的標籤：圖示＋字，不只靠顏色
    component CheckChip: Rectangle {
        id: chip

        property string checkState: "notChecked"

        readonly property string label: checkState === "available" ? qsTr("Available")
                                      : checkState === "failed" ? qsTr("Failed")
                                      : checkState === "checking" ? qsTr("Checking…")
                                      : qsTr("Not Checked")
        readonly property bool neutral: checkState !== "available" && checkState !== "failed"

        implicitWidth: chipContent.implicitWidth + 2 * Theme.spaceS
        implicitHeight: Theme.chipHeight
        radius: height / 2
        color: checkState === "available" ? Theme.successTint : checkState === "failed" ? Theme.errorTint : Theme.card
        border.width: neutral ? 1 : 0
        border.color: Theme.tileBorder
        Accessible.ignored: true    // 那一列的名字裡已經有狀態

        Row {
            id: chipContent
            anchors.centerIn: parent
            spacing: Theme.spaceXS

            Image {
                id: chipIcon
                objectName: "chipIcon"
                anchors.verticalCenter: parent.verticalCenter
                width: 14
                height: 14
                sourceSize: Qt.size(14, 14)
                source: chip.checkState === "available" ? "icons/circle-check_successOnDark.svg"
                      : chip.checkState === "failed" ? "icons/circle-x_errorOnDark.svg"
                      : chip.checkState === "checking" ? "icons/loader-circle_textSecondary.svg"
                      : "icons/circle-dashed_textPrimary.svg"

                // 檢查中轉圈（M-7）；減少動態時是靜止的 loader-circle（DS-43）
                RotationAnimator on rotation {
                    from: 0
                    to: 360
                    duration: Theme.durSpin
                    loops: Animation.Infinite
                    running: chip.checkState === "checking" && Theme.durSpin > 0
                }
            }

            Text {
                objectName: "chipLabel"
                anchors.verticalCenter: parent.verticalCenter
                text: chip.label
                color: chip.checkState === "available" ? Theme.successOnDark
                     : chip.checkState === "failed" ? Theme.errorOnDark
                     : chip.checkState === "checking" ? Theme.textSecondary
                     : Theme.textPrimary
                font.pixelSize: Theme.fontCaption
                font.weight: Font.DemiBold
            }
        }
    }

    // ── 一項檢查：名稱、結果與下一步、檢查時間；右邊是狀態標籤和 Details
    component CheckRow: Rectangle {
        id: row

        property var item: null
        property bool detailsExpanded: false

        readonly property string checkState: item ? item.state : "notChecked"
        readonly property string technical: item ? item.technical : ""
        readonly property bool hasResult: checkState === "available" || checkState === "failed"
        readonly property string chipLabel: chip.label

        implicitHeight: body.implicitHeight + 2 * 12
        radius: 12
        color: Theme.tile
        border.width: 1
        border.color: Theme.tileBorder
        Accessible.role: Accessible.Grouping
        Accessible.name: item ? qsTr("%1: %2").arg(item.label).arg(chip.label) : ""
        Accessible.description: item ? item.message : ""

        onHasResultChanged: {
            if (!hasResult)
                detailsExpanded = false     // 重新檢查時收起上一次的細節
        }

        Column {
            id: body
            x: Theme.spaceM
            y: 12
            width: row.width - 2 * Theme.spaceM
            spacing: 6

            Item {
                width: parent.width
                height: Math.max(texts.implicitHeight, side.implicitHeight)

                Column {
                    id: texts
                    width: parent.width - side.width - Theme.spaceM
                    spacing: 4

                    Text {
                        objectName: "checkLabel"
                        width: parent.width
                        text: row.item ? row.item.label : ""
                        color: Theme.textPrimary
                        font.pixelSize: Theme.fontBody
                        font.weight: Font.DemiBold
                        elide: Text.ElideRight
                        Accessible.ignored: true
                    }

                    Text {
                        objectName: "checkMessage"
                        width: parent.width
                        text: row.item ? row.item.message : ""
                        textFormat: Text.PlainText
                        color: Theme.textSecondary
                        font.pixelSize: Theme.fontCaption
                        wrapMode: Text.Wrap
                        Accessible.ignored: true
                    }

                    Text {
                        objectName: "checkTime"
                        width: parent.width
                        visible: row.hasResult && row.item.checkedAt !== ""
                        text: row.item ? qsTr("Checked %1").arg(row.item.checkedAt) : ""
                        color: Theme.textMuted
                        font.pixelSize: Theme.fontSmall
                        Accessible.ignored: true
                    }
                }

                Column {
                    id: side
                    x: parent.width - width
                    spacing: 4

                    CheckChip {
                        id: chip
                        objectName: "checkChip"
                        x: side.width - width
                        checkState: row.checkState
                    }

                    Button {
                        id: detailsButton
                        objectName: "checkDetailsButton"
                        x: side.width - width
                        visible: row.hasResult && row.technical !== ""
                        height: Theme.controlHeight
                        leftPadding: 4
                        rightPadding: 4
                        focusPolicy: Qt.StrongFocus
                        text: qsTr("Details")
                        font.pixelSize: Theme.fontCaption
                        onClicked: row.detailsExpanded = !row.detailsExpanded

                        Accessible.name: row.item ? qsTr("Details for %1").arg(row.item.label) : text
                        Accessible.expandable: true
                        Accessible.expanded: row.detailsExpanded

                        contentItem: Text {
                            text: detailsButton.text
                            font.pixelSize: detailsButton.font.pixelSize
                            font.underline: true
                            color: Theme.textSecondary
                            verticalAlignment: Text.AlignVCenter
                        }
                        background: Item {
                            Rectangle {     // 鍵盤焦點框（DS-30）
                                visible: detailsButton.visualFocus
                                anchors.fill: parent
                                anchors.margins: -2
                                radius: Theme.radiusS + 2
                                color: "transparent"
                                border.color: Theme.focusOnDark
                                border.width: 2
                            }
                        }
                    }
                }
            }

            Text {
                objectName: "checkTechnical"
                width: parent.width
                visible: row.detailsExpanded && row.hasResult && row.technical !== ""
                text: row.technical
                textFormat: Text.PlainText
                color: Theme.textSecondary
                font.pixelSize: Theme.fontSmall
                wrapMode: Text.WrapAnywhere
            }
        }
    }

    // ────────────────────────────────────────────── About
    SettingsGroup {
        id: aboutGroup
        objectName: "aboutGroup"
        title: qsTr("About")

        Column {
            width: aboutGroup.width
            spacing: 8

            InfoRow {
                objectName: "aboutVersion"
                width: parent.width
                labelWidth: help.labelWidth
                label: qsTr("Version")
                value: help.about.version || ""
            }

            InfoRow {
                objectName: "aboutPython"
                width: parent.width
                labelWidth: help.labelWidth
                label: qsTr("Python")
                value: help.about.python || ""
            }

            InfoRow {
                objectName: "aboutQt"
                width: parent.width
                labelWidth: help.labelWidth
                label: qsTr("Qt")
                value: help.about.qt || ""
            }

            InfoRow {
                objectName: "aboutSystem"
                width: parent.width
                labelWidth: help.labelWidth
                label: qsTr("System")
                value: help.about.system ? qsTr("%1 (%2)").arg(help.about.system).arg(help.about.architecture) : ""
            }
        }
    }

    SettingsDivider {}

    // ────────────────────────────────────────────── Connection
    SettingsGroup {
        id: connectionGroup
        objectName: "connectionGroup"
        title: qsTr("Connection")
        hint: qsTr("Copy Diagnostic Summary copies version numbers and check results only. It has no passwords, addresses or customer data, and nothing is sent.")

        Column {
            width: connectionGroup.width
            spacing: Theme.spaceS

            Text {
                width: parent.width
                text: qsTr("Check whether this computer can reach the database, cloud storage and the analysis service. Each check only tests whether that service answers.")
                color: Theme.textMuted
                font.pixelSize: Theme.fontSmall
                lineHeight: 18
                lineHeightMode: Text.FixedHeight
                wrapMode: Text.Wrap
            }

            CheckRow {
                objectName: "databaseCheck"
                width: parent.width
                item: help.checks.length > 0 ? help.checks[0] : null
            }

            CheckRow {
                objectName: "filesCheck"
                width: parent.width
                item: help.checks.length > 1 ? help.checks[1] : null
            }

            CheckRow {
                objectName: "analysisCheck"
                width: parent.width
                item: help.checks.length > 2 ? help.checks[2] : null
            }

            Item {
                width: parent.width
                height: 4 + connectionButtons.implicitHeight

                Flow {
                    id: connectionButtons
                    y: 4
                    width: parent.width
                    spacing: Theme.spaceS

                    BusyButton {
                        objectName: "checkConnectionButton"
                        variant: "primary"
                        enabled: help.connection !== null
                        text: qsTr("Check Connection")
                        busyText: qsTr("Checking…")
                        busy: help.connection ? help.connection.checking : false
                        onClicked: {
                            if (help.connection)
                                help.connection.check()
                        }
                    }

                    BusyButton {
                        objectName: "copySummaryButton"
                        enabled: help.center !== null
                        text: qsTr("Copy Diagnostic Summary")
                        onClicked: {
                            if (help.center)
                                help.center.copySummary()
                        }
                    }
                }
            }
        }
    }

    SettingsDivider {}

    // ────────────────────────────────────────────── Storage
    SettingsGroup {
        id: storageGroup
        objectName: "storageGroup"
        title: qsTr("Storage")
        hint: qsTr("The download cache holds copies of videos, charts and reports from cloud storage. Recordings aren't part of it.")

        Column {
            width: storageGroup.width
            spacing: 8

            InfoRow {
                objectName: "cacheSize"
                width: parent.width
                labelWidth: help.labelWidth
                label: qsTr("Download cache")
                value: help.storage ? help.storage.cacheText : ""
            }

            // 錄影不在下載快取裡（T51）：寫出它在哪裡，預設 ~/Movies/ADAM
            InfoRow {
                objectName: "recordingsFolder"
                width: parent.width
                labelWidth: help.labelWidth
                label: qsTr("Recordings folder")
                value: help.storage ? help.storage.recordingsFolder : ""
            }

            Repeater {
                model: help.storage ? help.storage.disks : []

                delegate: InfoRow {
                    required property var modelData
                    objectName: "freeSpace"
                    width: storageGroup.width
                    labelWidth: help.labelWidth
                    label: modelData.label
                    value: modelData.text
                }
            }

            Item {
                width: parent.width
                height: 8 + clearButton.implicitHeight

                BusyButton {
                    id: clearButton
                    objectName: "clearCacheButton"
                    y: 8
                    // 正在清的時候保持可以按（busy 擋住點擊），焦點不會掉
                    enabled: help.storage ? help.storage.canClear || help.storage.clearing : false
                    text: qsTr("Clear Download Cache")
                    busyText: qsTr("Clearing…")
                    busy: help.storage ? help.storage.clearing : false
                    onClicked: clearDialog.open()
                }
            }

            Text {
                objectName: "clearBlockedReason"
                width: parent.width
                visible: text !== ""
                text: help.storage ? help.storage.blockedReason : ""
                color: Theme.textMuted
                font.pixelSize: Theme.fontSmall
                wrapMode: Text.Wrap
            }

            Row {
                objectName: "clearResult"
                width: parent.width
                visible: help.storage !== null && help.storage.resultText !== ""
                spacing: 8
                Accessible.role: Accessible.AlertMessage
                Accessible.name: help.storage ? help.storage.resultText : ""

                Image {
                    width: Theme.iconS
                    height: Theme.iconS
                    sourceSize: Qt.size(Theme.iconS, Theme.iconS)
                    source: "icons/circle-alert_errorOnDark.svg"
                    Accessible.ignored: true
                }

                Text {
                    objectName: "clearResultText"
                    width: parent.width - Theme.iconS - parent.spacing
                    text: help.storage ? help.storage.resultText : ""
                    color: Theme.errorOnDark
                    font.pixelSize: Theme.fontSmall
                    wrapMode: Text.Wrap
                    Accessible.ignored: true
                }
            }

            ConfirmDialog {
                id: clearDialog
                objectName: "clearCacheDialog"
                title: qsTr("Clear the download cache?")
                body: qsTr("This removes %1 of downloaded videos, charts and reports from this computer. They download again when you open them. Recordings, unfinished uploads and cloud storage aren't changed.")
                      .arg(help.storage ? help.storage.cacheText : "")
                confirmText: qsTr("Clear Cache")
                cancelText: qsTr("Keep Cache")
                onConfirmed: {
                    if (help.storage)
                        help.storage.clear()
                }
            }
        }
    }
}
