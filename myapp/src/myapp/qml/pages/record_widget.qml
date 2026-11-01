pragma ComponentBehavior: Bound
import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import QtQuick.Controls.Material 2.15
import "../components"

// 錄影頁：選鏡頭和麥克風（UX-13、UX-14、ENG-06）。
// - 打開這一頁才探測裝置（以前登入時就把 0～9 號鏡頭都打開一次），結果留著；「Scan Again」重新探測。
// - 探測中、找不到足夠的裝置（空狀態）、探測失敗（區塊錯誤）各有自己的畫面。
// - 員工鏡頭預設選第二支；兩支相同時顯示欄位錯誤，Activate 停用。
// - Activate 打開的錄影視窗（RecordWidget）留給第三波。
Item {
    id: recordPage
    anchors.fill: parent
    Material.theme: Material.Light
    Material.accent: Material.Blue

    readonly property var finder: typeof deviceFinder !== "undefined" ? deviceFinder : null
    readonly property string status: finder ? finder.status : "idle"
    readonly property int cameraCount: finder ? finder.cameras.length : 0
    readonly property int microphoneCount: finder ? finder.microphones.length : 0
    readonly property bool enoughDevices: cameraCount >= 2 && microphoneCount >= 1
    readonly property bool sameCamera: cam0ComboBox.currentIndex === cam1ComboBox.currentIndex

    function resetSelection() {
        cam0ComboBox.currentIndex = 0
        cam1ComboBox.currentIndex = cameraCount > 1 ? 1 : 0     // 預設就是兩支不同的鏡頭
        micComboBox.currentIndex = 0
    }

    Component.onCompleted: {
        resetSelection()
        if (finder)
            finder.ensureScanned()
    }

    Connections {
        target: recordPage.finder
        function onCamerasChanged() { recordPage.resetSelection() }
        function onMicrophonesChanged() { micComboBox.currentIndex = 0 }
    }

    component FieldLabel: Text {
        Layout.alignment: Qt.AlignLeft
        color: Theme.textPrimary
        font.pixelSize: Theme.fontBodyLarge
        font.weight: Font.DemiBold
    }

    // 探測中
    Column {
        objectName: "scanningState"
        anchors.centerIn: parent
        spacing: Theme.spaceS
        visible: recordPage.status === "idle" || recordPage.status === "scanning"
        Accessible.role: Accessible.StaticText
        Accessible.name: scanningText.text

        Image {
            id: spinner
            anchors.horizontalCenter: parent.horizontalCenter
            width: Theme.iconM
            height: Theme.iconM
            sourceSize: Qt.size(Theme.iconM, Theme.iconM)
            source: "../components/icons/loader-circle_textSecondary.svg"
            Accessible.ignored: true

            RotationAnimator on rotation {
                from: 0
                to: 360
                duration: Theme.durSpin
                loops: Animation.Infinite
                running: spinner.visible && Theme.durSpin > 0
            }
        }

        Text {
            id: scanningText
            text: qsTr("Looking for cameras and microphones…")
            color: Theme.textSecondary
            font.pixelSize: Theme.fontBody
        }
    }

    // 探測失敗（例如音訊系統初始化失敗）
    InlineError {
        objectName: "scanError"
        anchors.centerIn: parent
        width: Math.min(parent.width, 480)
        visible: recordPage.status === "error"
        title: qsTr("Couldn't check cameras and microphones")
        detail: qsTr("Check that they're connected, then try again.")
        technicalDetail: recordPage.finder ? recordPage.finder.errorTechnical : ""
        retryText: qsTr("Scan Again")
        onRetry: recordPage.finder.rescan()
    }

    // 裝置不夠，錄不了影
    EmptyState {
        objectName: "devicesEmpty"
        anchors.centerIn: parent
        width: Math.min(parent.width, 360)
        visible: recordPage.status === "ready" && !recordPage.enoughDevices
        title: recordPage.cameraCount === 0 ? qsTr("No cameras found")
             : recordPage.cameraCount === 1 ? qsTr("Only one camera found")
             : qsTr("No microphone found")
        description: recordPage.cameraCount === 0 ? qsTr("Connect the customer and staff cameras, then scan again.")
                   : recordPage.cameraCount === 1 ? qsTr("Recording needs a customer camera and a staff camera. Connect another camera, then scan again.")
                   : qsTr("Connect a microphone, then scan again.")
        actionText: qsTr("Scan Again")
        onActionTriggered: recordPage.finder.rescan()
    }

    ColumnLayout {
        objectName: "deviceForm"
        anchors.centerIn: parent
        width: 420
        spacing: Theme.spaceS
        visible: recordPage.status === "ready" && recordPage.enoughDevices

        FieldLabel { text: qsTr("Customer Camera") }
        ComboBox {
            id: cam0ComboBox
            objectName: "customerCamera"
            Layout.fillWidth: true
            model: recordPage.finder ? recordPage.finder.cameras : []
            font.pixelSize: Theme.fontBody
            Accessible.name: qsTr("Customer camera")
        }

        FieldLabel {
            Layout.topMargin: Theme.spaceS
            text: qsTr("Staff Camera")
        }
        ComboBox {
            id: cam1ComboBox
            objectName: "staffCamera"
            Layout.fillWidth: true
            model: recordPage.finder ? recordPage.finder.cameras : []
            font.pixelSize: Theme.fontBody
            Accessible.name: qsTr("Staff camera")
            Accessible.description: sameCameraError.text
        }
        FieldError {
            id: sameCameraError
            objectName: "sameCameraError"
            Layout.fillWidth: true
            Layout.preferredHeight: 2 * lineHeight  // 這句會排成兩行：先留好，錯誤出現時版面不跳
            text: recordPage.sameCamera ? qsTr("The staff and customer cameras are the same. Choose a different staff camera.") : ""
        }

        FieldLabel { text: qsTr("Microphone") }
        ComboBox {
            id: micComboBox
            objectName: "microphone"
            Layout.fillWidth: true
            model: recordPage.finder ? recordPage.finder.microphones : []
            font.pixelSize: Theme.fontBody
            Accessible.name: qsTr("Microphone")
        }

        BusyButton {
            objectName: "activateButton"
            Layout.fillWidth: true
            Layout.topMargin: Theme.spaceL
            variant: "primary"
            large: true
            text: qsTr("Activate")
            enabled: !recordPage.sameCamera     // 選項不合法時停用，原因寫在欄位下方
            onClicked: {
                if (busy)
                    return
                recordPage.finder.startRecording(cam0ComboBox.currentIndex, cam1ComboBox.currentIndex,
                                                 micComboBox.currentIndex)
            }
        }

        BusyButton {
            objectName: "rescanButton"
            Layout.alignment: Qt.AlignHCenter
            text: qsTr("Scan Again")
            onClicked: recordPage.finder.rescan()
        }
    }
}
