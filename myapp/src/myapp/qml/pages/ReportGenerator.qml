pragma ComponentBehavior: Bound
import QtQuick
// 自訂了 background、contentItem，要用 Basic 樣式（T13）
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "../components"

// 產生報表的表單（管理員的報表頁，new-feature N4、N5，T37）：按 New Report 才打開（PLAN 13.1：需要時才展開的表單）。
// - Type：Service（一次服務）、Staff（一位員工、一段期間）、Company（全公司、一段期間，只有管理員看得到）。
// - Service：先選員工，再選他最近分析完成的服務（reportsModel.loadServices，新的在前）。
//   其他的服務從 Services 頁打開明細，在明細頁的報表卡產生。
// - Staff、Company：From、To（DateField：只用它的 text、invalid）。預設是這個月 1 號到今天；一天依 Pacific Time 切（R4）。
// - Generate Report：檢查欄位（reportsModel.generate），新增成功（reportCreated）就關掉，清單上那一列是 Generating。
//   同一個對象已經在產生中時按鈕是忙碌的（generatingKeys），不會重複產生。
// - 檢查、資料庫、HTTP 都在 Python（db lane、io lane），這裡不碰網路。
Dialog {
    id: root

    property var vm: null
    property string kind: "service"
    property string staffId: ""
    property string serviceId: ""

    readonly property bool needsStaff: kind !== "company"
    readonly property bool needsPeriod: kind !== "service"
    // 和 Python 的 Subject.key 一樣：同一個對象正在產生時，按鈕忙碌
    readonly property string subjectKey: kind === "service" ? "service|" + serviceId
        : kind === "staff" ? "staff|" + staffId + "|" + startField.text.trim() + "|" + endField.text.trim()
        : "company|" + startField.text.trim() + "|" + endField.text.trim()
    readonly property bool alreadyGenerating: vm !== null && vm.generatingKeys.indexOf(subjectKey) >= 0
    readonly property bool servicesHere: vm !== null && vm.servicesFor === staffId && staffId !== ""
    // 欄位（測試從外面操作用）
    readonly property alias staffField: staffSelect
    readonly property alias serviceField: serviceSelect
    readonly property alias startDateField: startField
    readonly property alias endDateField: endField
    readonly property alias generateButton: generateButton

    title: qsTr("New Report")
    modal: true
    focus: true
    closePolicy: Popup.CloseOnEscape
    z: Theme.zDialog
    anchors.centerIn: Overlay.overlay
    width: Math.min(560, Overlay.overlay ? Overlay.overlay.width - 2 * Theme.spaceXL : 560)
    padding: Theme.spaceXL
    header: null
    footer: null

    function submit() {
        if (!vm || vm.submitting || alreadyGenerating)
            return
        vm.generate(kind, staffId, serviceId, startField.text, endField.text)
    }

    function chooseKind(value) {
        if (kind === value)
            return
        kind = value
        if (vm)
            vm.clearFormError("subject")
        if (kind === "service" && staffId !== "" && vm)
            vm.loadServices(staffId)
    }

    // 選單的第一項是「還沒選」：HomeSelect 找不到 value 時會顯示第一項，所以第一項要真的是空的
    function withPlaceholder(options, placeholder) {
        return [{ value: "", text: placeholder }].concat(options)
    }

    onAboutToShow: {
        if (!vm)
            return
        vm.loadStaff()
        if (startField.text === "" && endField.text === "") {
            const period = vm.defaultPeriod()
            startField.text = period.start
            endField.text = period.end
        }
        if (kind === "service" && staffId !== "")
            vm.loadServices(staffId)
    }

    Connections {
        target: root.vm
        function onReportCreated() {
            root.close()
        }
    }

    background: Rectangle {
        color: Theme.card
        radius: Theme.radiusCard
        border.color: Theme.cardBorder
        border.width: 1
    }

    Overlay.modal: Rectangle {
        color: Theme.scrim
    }

    // 欄位上面的標籤
    component FieldLabel: Text {
        color: Theme.textSecondary
        font.pixelSize: Theme.fontCaption
        font.weight: Font.DemiBold
    }

    // 種類：分段切換（和分析頁的分頁同一個樣子）
    component KindTab: Button {
        id: tab
        required property string value
        readonly property bool selected: root.kind === value

        implicitHeight: Theme.controlHeight
        leftPadding: 18
        rightPadding: 18
        verticalPadding: 0
        focusPolicy: Qt.StrongFocus
        font.pixelSize: Theme.fontCaption
        font.weight: Font.DemiBold
        Accessible.role: Accessible.RadioButton
        Accessible.name: text
        Accessible.checkable: true
        Accessible.checked: selected
        Keys.onReturnPressed: click()
        Keys.onEnterPressed: click()
        onClicked: root.chooseKind(value)

        contentItem: Text {
            text: tab.text
            font: tab.font
            color: tab.selected ? Theme.segmentSelectedText : Theme.segmentIdleText
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }

        background: Rectangle {
            radius: 12
            color: tab.selected ? Theme.segmentSelected : tab.hovered ? Theme.actionSecondaryHover : "transparent"

            Rectangle {     // 鍵盤焦點框（DS-30）
                visible: tab.visualFocus
                anchors.fill: parent
                anchors.margins: -2
                radius: parent.radius + 2
                color: "transparent"
                border.color: Theme.focusOnDark
                border.width: 2
            }
        }
    }

    contentItem: ColumnLayout {
        spacing: Theme.spaceM

        Text {
            objectName: "dialogTitle"
            Layout.fillWidth: true
            text: root.title
            color: Theme.textPrimary
            font.pixelSize: Theme.fontTitleSmall
            font.weight: Font.Bold
            Accessible.role: Accessible.Heading
            Accessible.name: text
        }

        // ── 種類
        ColumnLayout {
            Layout.fillWidth: true
            spacing: 8

            FieldLabel { text: qsTr("Type") }

            Rectangle {     // 分段的外框
                Layout.alignment: Qt.AlignLeft
                implicitWidth: kindRow.implicitWidth + 2
                implicitHeight: kindRow.implicitHeight + 2
                radius: 13
                color: Theme.segmentBg
                border.color: Theme.cardBorder
                border.width: 1

                Row {
                    id: kindRow
                    x: 1
                    y: 1
                    Accessible.role: Accessible.Grouping
                    Accessible.name: qsTr("Report type")

                    KindTab { objectName: "serviceKindTab"; value: "service"; text: qsTr("Service") }
                    KindTab { objectName: "staffKindTab"; value: "staff"; text: qsTr("Staff") }
                    KindTab { objectName: "companyKindTab"; value: "company"; text: qsTr("Company") }
                }
            }

            Text {
                objectName: "kindHint"
                Layout.fillWidth: true
                text: root.kind === "service" ? qsTr("Scores for one service, with audio, text, and facial results.")
                    : root.kind === "staff" ? qsTr("One staff member's results over a period.")
                    : qsTr("All staff over a period. Only admins can see company reports.")
                color: Theme.textMuted
                font.pixelSize: Theme.fontCaption
                wrapMode: Text.Wrap
            }
        }

        // ── 對象：員工、服務（每次服務報表兩欄一樣寬）
        GridLayout {
            id: subjectGrid
            readonly property real half: Math.floor((root.availableWidth - columnSpacing) / 2)  // 用對話框的寬度：自己的寬度會跟著內容變
            Layout.fillWidth: true
            visible: root.needsStaff
            columns: root.kind === "service" ? 2 : 1
            columnSpacing: Theme.spaceM
            rowSpacing: 8

            FieldLabel { text: qsTr("Staff") }
            FieldLabel { visible: root.kind === "service"; text: qsTr("Service") }

            HomeSelect {
                id: staffSelect
                objectName: "staffSelect"
                Layout.fillWidth: root.kind !== "service"
                Layout.preferredWidth: root.kind === "service" ? subjectGrid.half : -1
                enabled: root.vm !== null && root.vm.staffState === "ready"
                options: root.withPlaceholder(root.vm ? root.vm.staffOptions : [],
                                              root.vm && root.vm.staffState === "loading" ? qsTr("Loading…")
                                                                                          : qsTr("Choose a staff member"))
                value: root.staffId
                Accessible.name: qsTr("Staff")
                onPicked: (value) => {
                    root.staffId = value
                    root.serviceId = ""
                    if (root.vm) {
                        root.vm.clearFormError("subject")
                        if (root.kind === "service" && value !== "")
                            root.vm.loadServices(value)
                    }
                }
            }

            HomeSelect {
                id: serviceSelect
                objectName: "serviceSelect"
                Layout.preferredWidth: subjectGrid.half
                visible: root.kind === "service"
                enabled: root.servicesHere && root.vm.servicesState === "ready"
                options: root.withPlaceholder(root.servicesHere ? root.vm.serviceOptions : [],
                                              root.servicesHere && root.vm.servicesState === "loading"
                                              ? qsTr("Loading…") : qsTr("Choose a service"))
                value: root.serviceId
                Accessible.name: qsTr("Service")
                onPicked: (value) => {
                    root.serviceId = value
                    if (root.vm)
                        root.vm.clearFormError("subject")
                }
            }
        }

        FieldError {
            objectName: "subjectError"
            Layout.fillWidth: true
            visible: hasError
            text: {
                if (!root.vm)
                    return ""
                if (root.vm.subjectError !== "")
                    return root.vm.subjectError
                if (root.needsStaff && root.vm.staffState === "error")
                    return qsTr("Couldn't load the staff list. Close this form and open it again to retry.")
                if (root.needsStaff && root.vm.staffState === "empty")
                    return qsTr("There are no staff accounts yet.")
                if (root.kind === "service" && root.servicesHere && root.vm.servicesState === "error")
                    return qsTr("Couldn't load this staff member's services. Choose them again to retry.")
                if (root.kind === "service" && root.servicesHere && root.vm.servicesState === "empty")
                    return qsTr("This staff member has no completed services.")
                return ""
            }
        }

        // ── 期間（員工、全公司）：兩欄一樣寬（錯誤的字很長時也不會把另一欄擠窄）
        GridLayout {
            id: periodGrid
            readonly property real half: Math.floor((root.availableWidth - columnSpacing) / 2)  // 用對話框的寬度：自己的寬度會跟著內容變
            Layout.fillWidth: true
            visible: root.needsPeriod
            columns: 2
            columnSpacing: Theme.spaceM
            rowSpacing: 8

            FieldLabel { text: qsTr("From") }
            FieldLabel { text: qsTr("To") }

            // 日期欄和 Services 頁共用（DateField.qml）：只用它的 text、invalid
            DateField {
                id: startField
                objectName: "reportStartField"
                Layout.preferredWidth: periodGrid.half
                invalid: root.vm !== null && root.vm.startDateError !== ""
                Accessible.name: qsTr("From date")
                onTextChanged: {
                    if (root.vm && root.vm.startDateError !== "")
                        root.vm.clearFormError("start")
                }
            }

            DateField {
                id: endField
                objectName: "reportEndField"
                Layout.preferredWidth: periodGrid.half
                invalid: root.vm !== null && root.vm.endDateError !== ""
                Accessible.name: qsTr("To date")
                onTextChanged: {
                    if (root.vm && root.vm.endDateError !== "")
                        root.vm.clearFormError("end")
                }
            }

            // 欄位錯誤：有錯的時候才佔位置（T31 第 8 節）。兩格一起出現、一起收起，格子才不會換欄
            FieldError {
                id: startError
                objectName: "startDateError"
                Layout.preferredWidth: periodGrid.half
                visible: hasError || endError.hasError
                text: root.vm ? root.vm.startDateError : ""
            }

            FieldError {
                id: endError
                objectName: "endDateError"
                Layout.preferredWidth: periodGrid.half
                visible: hasError || startError.hasError
                text: root.vm ? root.vm.endDateError : ""
            }
        }

        Text {
            objectName: "periodHint"
            Layout.fillWidth: true
            visible: root.needsPeriod
            text: qsTr("Both days are included. Days follow Pacific Time.")
            color: Theme.textMuted
            font.pixelSize: Theme.fontSmall
            wrapMode: Text.Wrap
        }

        // 新增失敗（例如連不上資料庫、那筆服務不見了）：寫在按鈕上面，表單的內容留著
        FieldError {
            objectName: "formError"
            Layout.fillWidth: true
            visible: hasError
            text: root.vm ? root.vm.formError : ""
        }

        RowLayout {
            Layout.alignment: Qt.AlignRight
            Layout.topMargin: 8
            spacing: 12

            BusyButton {
                objectName: "closeGeneratorButton"
                text: qsTr("Close")
                onClicked: root.close()
            }

            BusyButton {
                id: generateButton
                objectName: "generateButton"
                variant: "primary"
                text: qsTr("Generate Report")
                busyText: qsTr("Generating…")
                busy: root.vm !== null && (root.vm.submitting || root.alreadyGenerating)
                onClicked: root.submit()
            }
        }
    }
}
