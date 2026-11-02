import QtQuick
import QtQuick.Layouts

// 頁首（new-UIUX PLAN 第 4 節、6-1）：兩個外殼共用，在內容區最上面，和頁面同寬。
// - 左邊：首頁是 ADAM 字標；其他頁是頁面標題和一句說明（這一頁做什麼）；明細頁前面多一顆「‹ Services」。
// - 右邊：放在 PageHeader 裡的子項目（搜尋服務 ID，UD5）、主要動作（只有首頁有，UD12）、頭像和名字。
//   頭像：有照片（userPhoto，帳號在設定頁換的，new-feature N3）時是圓形的照片，沒有或載入失敗時是預設的圖示。
//   new-feature PLAN 寫「側邊欄顯示」：U0 已經把頭像從側邊欄搬到頁首，所以照片在這裡（T36）。
// 標題、說明、上一頁由外殼依目前的頁面決定；頁面要自己給（明細頁的服務 ID）時，外殼讀頁面的
// headerTitle、headerSubtitle、headerBackText，按「‹」呼叫頁面的 goBack()。
RowLayout {
    id: header

    property bool wordmark: false
    property string title: ""
    property string subtitle: ""
    property string backText: ""            // 不是空的：標題左邊有「‹ backText」，按了發出 back()
    property string actionText: ""          // 不是空的：主要動作按鈕
    property url actionIcon: ""
    property string userName: ""
    property string userRole: ""
    property url userPhoto: ""              // 圓形的照片（本機快取的 PNG，外面透明）；空的＝預設的圖示
    default property alias extras: extraSlot.data

    signal back()
    signal actionClicked()

    spacing: Theme.spaceL

    BusyButton {
        id: backButton
        objectName: "headerBack"
        visible: header.backText !== ""
        text: header.backText
        iconSource: "icons/chevron-left_actionSecondaryText.svg"
        Accessible.name: qsTr("Back to %1").arg(header.backText)
        onClicked: header.back()
    }

    Row {       // ADAM 字標：和登入頁同一個字標，小一號
        objectName: "headerWordmark"
        visible: header.wordmark
        spacing: 12
        Accessible.role: Accessible.Heading
        Accessible.name: qsTr("ADAM")

        Rectangle {
            anchors.verticalCenter: parent.verticalCenter
            width: 36
            height: 36
            radius: 9
            color: Theme.actionPrimary
            Accessible.ignored: true

            Text {
                anchors.centerIn: parent
                text: qsTr("A")
                color: Theme.actionPrimaryText
                font.pixelSize: Theme.fontTitleSmall
                font.weight: Font.Black
                Accessible.ignored: true
            }
        }

        Text {
            anchors.verticalCenter: parent.verticalCenter
            text: qsTr("ADAM")
            color: Theme.textTitle
            font.pixelSize: Theme.fontPageTitle
            font.weight: Font.Light
            font.letterSpacing: 7
            Accessible.ignored: true
        }
    }

    ColumnLayout {
        visible: !header.wordmark
        Layout.fillWidth: true
        spacing: 2

        Text {
            objectName: "headerTitle"
            Layout.fillWidth: true
            text: header.title
            color: Theme.textTitle
            font.pixelSize: Theme.fontPageTitle
            font.bold: true
            font.features: { "tnum": 1 }    // 明細頁的標題是服務 ID
            elide: Text.ElideRight
            Accessible.role: Accessible.Heading
            Accessible.name: text
        }

        Text {
            objectName: "headerSubtitle"
            Layout.fillWidth: true
            visible: text !== ""
            text: header.subtitle
            color: Theme.textMuted
            font.pixelSize: Theme.fontCaption
            elide: Text.ElideRight
        }
    }

    Item {      // 首頁沒有標題欄：字標和右邊之間的空白
        visible: header.wordmark
        Layout.fillWidth: true
    }

    RowLayout {
        id: extraSlot
        spacing: Theme.spaceL
    }

    BusyButton {
        objectName: "headerAction"
        visible: header.actionText !== ""
        variant: "primary"
        text: header.actionText
        iconSource: header.actionIcon
        onClicked: header.actionClicked()
    }

    Row {       // 目前登入的人：頭像只是裝飾，名字和身分給螢幕閱讀器
        objectName: "headerUser"
        spacing: Theme.spaceS
        Accessible.role: Accessible.StaticText
        Accessible.name: header.userRole === "" ? header.userName : header.userName + ", " + header.userRole

        Rectangle {
            anchors.verticalCenter: parent.verticalCenter
            width: Theme.controlHeight
            height: Theme.controlHeight
            radius: width / 2
            color: Theme.actionSecondaryBg
            border.color: Theme.tileBorder
            border.width: 1
            Accessible.ignored: true

            Image {
                objectName: "headerAvatarIcon"
                anchors.centerIn: parent
                visible: !headerPhoto.visible
                width: 24
                height: 24
                sourceSize: Qt.size(24, 24)
                source: "icons/circle-user-round_iconNeutral.svg"
                Accessible.ignored: true
            }

            // 照片已經是圓形的 PNG（Python 在 io lane 做好），這裡不需要遮罩或 shader
            Image {
                id: headerPhoto
                objectName: "headerPhoto"
                anchors.fill: parent
                visible: header.userPhoto.toString() !== "" && status === Image.Ready
                source: header.userPhoto
                sourceSize: Qt.size(2 * Theme.controlHeight, 2 * Theme.controlHeight)    // Retina 上也清楚
                fillMode: Image.PreserveAspectFit
                asynchronous: true
                smooth: true
                mipmap: true
                Accessible.ignored: true
            }
        }

        Column {
            anchors.verticalCenter: parent.verticalCenter

            Text {
                objectName: "headerUserName"
                text: header.userName
                color: Theme.textPrimary
                font.pixelSize: Theme.fontCaption
                font.weight: Font.DemiBold
                Accessible.ignored: true
            }

            Text {
                objectName: "headerUserRole"
                text: header.userRole
                color: Theme.textFaint
                font.pixelSize: Theme.fontLabel
                Accessible.ignored: true
            }
        }
    }
}
