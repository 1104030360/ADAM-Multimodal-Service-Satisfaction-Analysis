pragma ComponentBehavior: Bound
import QtQuick
// 自訂了 background、contentItem，要用 Basic 樣式（T13）
import QtQuick.Controls.Basic

// 一則通知（DS-21），ToastHost 的 delegate。
// 佇列、去重、上限、預設停留時間都由 Python 的 Notifier 決定；這裡只管畫面和計時：
// - timeoutMs 0 是常駐。滑鼠停在上面、焦點在裡面、視窗不在前景時暫停，回來後重新計時。
// - 同一個 key 就地更新時 revision 加 1，重新計時。
// - 出現時唸出來：錯誤用 Assertive，其他用 Polite；進度只唸開始和結束。
// - 按鈕不搶焦點（只有 Tab 進得來），按 Esc 關掉這一則。
Control {
    id: root

    // Notifier 模型的角色（CONTRACTS §1），由 ListView 依名稱填入
    required property int toastId
    required property string severity       // success、info、warning、error、progress
    required property string title
    required property string detail
    required property string actionLabel    // 空字串＝沒有動作
    required property string actionId
    required property int timeoutMs         // 0＝常駐
    required property real done             // progress 用，bytes
    required property real total            // 0＝不知道總量
    required property int count             // 同一個 key 收到幾次
    required property int revision          // 就地更新時加 1

    signal dismissRequested(int toastId)
    signal actionRequested(int toastId)

    readonly property bool isError: severity === "error"
    readonly property bool isProgress: severity === "progress"
    // Control 本身不是 focus scope；contentItem 是 FocusScope，焦點在裡面的按鈕時它的 activeFocus 才會是 true
    readonly property bool paused: hover.hovered || content.activeFocus || !root.Window.active
    // 進度的數字接在 detail 前面：「58.6 / 124.0 MB · 47% · File 2 of 3」
    readonly property string bodyText: [isProgress ? Theme.transferText(done, total) : "", detail]
        .filter(part => part !== "").join(" · ")
    property var announced: []      // 唸過的內容 { text, assertive }，測試和除錯用

    width: Theme.toastWidth
    leftPadding: Theme.spaceM
    rightPadding: Theme.spaceM - (Theme.hitMin - Theme.iconS) / 2   // 關閉鈕自己有留白，x 圖示到右框還是 16
    topPadding: 12
    bottomPadding: 12

    Accessible.role: isError ? Accessible.AlertMessage : Accessible.Notification
    Accessible.name: title
    Accessible.description: bodyText

    background: Rectangle {
        color: Theme.toastBg
        border.color: Theme.toastBorder     // 蓋在黑色影片上時靠框線分開（DS-16），不加模糊陰影
        border.width: 1
        radius: Theme.radiusM
    }

    contentItem: FocusScope {
        id: content
        implicitWidth: Theme.toastWidth - root.leftPadding - root.rightPadding
        implicitHeight: Math.max(textColumn.implicitHeight, closeButton.height)

        Image {
            objectName: "toastIcon"
            y: 2    // 和標題第一行置中
            width: Theme.iconM
            height: Theme.iconM
            sourceSize: Qt.size(Theme.iconM, Theme.iconM)
            source: root.severity === "success" ? "icons/circle-check_successOnDark.svg"
                  : root.severity === "warning" ? "icons/triangle-alert_warningOnDark.svg"
                  : root.severity === "error" ? "icons/circle-alert_errorOnDark.svg"
                  : root.severity === "progress" ? "icons/upload_chromeIcon.svg"
                  : "icons/info_infoOnDark.svg"
            Accessible.ignored: true
        }

        Column {
            id: textColumn
            x: Theme.iconM + 12
            width: parent.width - x - 12 - closeButton.width
            spacing: 2

            Text {
                objectName: "toastTitle"
                width: parent.width
                text: root.title
                color: Theme.toastTitle
                font.pixelSize: Theme.fontBody
                font.weight: Font.DemiBold
                wrapMode: Text.Wrap
                maximumLineCount: 2
                elide: Text.ElideRight
            }

            Text {
                objectName: "toastBody"
                width: parent.width
                visible: text !== ""
                text: root.bodyText
                color: Theme.toastText
                font.pixelSize: Theme.fontCaption
                font.features: { "tnum": 1 }
                wrapMode: Text.Wrap
                maximumLineCount: 2     // 最多兩行（DS-21）
                elide: Text.ElideRight
            }

            Item {
                visible: root.isProgress
                width: parent.width
                height: 6 + progress.height

                TransferProgress {
                    id: progress
                    objectName: "toastProgress"
                    y: 6
                    width: parent.width
                    onDark: true
                    done: root.done
                    total: root.total
                    label: root.title
                }
            }

            Item {
                visible: root.actionLabel !== ""
                width: parent.width
                height: 6 + actionButton.height

                Button {
                    id: actionButton
                    objectName: "toastAction"
                    y: 6
                    height: 32
                    leftPadding: 12
                    rightPadding: 12
                    focusPolicy: Qt.TabFocus    // 滑鼠按不會把焦點搶過來
                    text: root.actionLabel
                    font.pixelSize: Theme.fontCaption
                    font.weight: Font.DemiBold
                    onClicked: root.actionRequested(root.toastId)

                    contentItem: Text {
                        text: actionButton.text
                        font: actionButton.font
                        color: Theme.toastTitle
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                    }
                    background: Rectangle {
                        radius: Theme.radiusS
                        color: actionButton.down ? Theme.toastButtonPressed
                             : actionButton.hovered ? Theme.toastButtonHover : "transparent"
                        border.color: Theme.toastBorder
                        border.width: 1

                        Rectangle {     // 鍵盤焦點框（DS-30）：2 px、往外 2 px
                            visible: actionButton.visualFocus
                            anchors.fill: parent
                            anchors.margins: -4
                            radius: parent.radius + 4
                            color: "transparent"
                            border.color: Theme.focusOnDark
                            border.width: 2
                        }
                    }
                }
            }
        }

        Button {
            id: closeButton
            objectName: "toastClose"
            anchors.right: parent.right
            width: Theme.hitMin
            height: Theme.hitMin
            padding: 0
            focusPolicy: Qt.TabFocus
            onClicked: root.dismissRequested(root.toastId)

            Accessible.name: qsTr("Close notification")

            contentItem: Item {
                Image {
                    anchors.centerIn: parent
                    width: Theme.iconS
                    height: Theme.iconS
                    sourceSize: Qt.size(Theme.iconS, Theme.iconS)
                    source: "icons/x_chromeIcon.svg"
                    Accessible.ignored: true
                }
            }
            background: Rectangle {
                radius: Theme.radiusS
                color: closeButton.down ? Theme.toastButtonPressed
                     : closeButton.hovered ? Theme.toastButtonHover : "transparent"

                Rectangle {
                    visible: closeButton.visualFocus
                    anchors.fill: parent
                    anchors.margins: -4
                    radius: parent.radius + 4
                    color: "transparent"
                    border.color: Theme.focusOnDark
                    border.width: 2
                }
            }
        }
    }

    HoverHandler { id: hover }

    Keys.onEscapePressed: root.dismissRequested(root.toastId)

    Timer {
        id: life
        objectName: "lifeTimer"
        interval: Math.max(1, root.timeoutMs)
        running: root.timeoutMs > 0 && !root.paused
        onTriggered: root.dismissRequested(root.toastId)
    }

    // 就地更新時好幾個角色會一起變；等它們都改完（下一輪事件）再重新計時、決定要不要唸
    Timer {
        id: refresh
        objectName: "refreshTimer"
        interval: 0
        onTriggered: {
            if (life.running)
                life.restart()
            if (!root.isProgress)       // 進度更新不唸；變成成功或錯誤（結束）時才唸
                root.announce()
        }
    }
    // 建立時填入角色也會觸發 changed，那時還不算更新
    QtObject {
        id: internal
        property bool ready: false
    }
    onRevisionChanged: if (internal.ready) refresh.restart()
    onSeverityChanged: if (internal.ready) refresh.restart()

    function announce() {
        const text = isProgress || detail === "" ? title : title + ". " + detail
        const entry = { text: text, assertive: isError }
        announced = announced.concat([entry])
        Accessible.announce(text, isError ? Accessible.Assertive : Accessible.Polite)
    }

    Component.onCompleted: {
        internal.ready = true
        announce()
    }
}
