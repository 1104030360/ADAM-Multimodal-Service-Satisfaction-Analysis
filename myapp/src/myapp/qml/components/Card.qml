import QtQuick
// 自訂了 background，要用 Basic 樣式（T13）
import QtQuick.Controls.Basic

// 卡片（new-UIUX PLAN 第 4 節）：深色底、1 px 框線、圓角 16、內距上下 24、左右 28。
// 裡面放一個項目（通常是 ColumnLayout），卡片的大小跟著它的 implicit size（Pane 的行為）。
// 標題、說明、右邊的控制項由頁面自己排：各頁不一樣，做成屬性反而綁手綁腳。
Pane {
    id: card

    property color fill: Theme.card             // 趨勢卡換成藍底（UD4）
    property color borderColor: Theme.cardBorder

    topPadding: Theme.cardPaddingV
    bottomPadding: Theme.cardPaddingV
    leftPadding: Theme.cardPaddingH
    rightPadding: Theme.cardPaddingH

    background: Rectangle {
        objectName: "cardBackground"
        color: card.fill
        radius: Theme.radiusCard
        border.color: card.borderColor
        border.width: 1
    }
}
