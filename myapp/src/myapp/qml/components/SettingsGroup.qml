import QtQuick

// 設定頁的一組選項：標題、一排方塊（SettingsOption，由使用的地方平分寬度）、下面可以有一句說明（新版介面 PLAN 6-7）。
// 也可以放其他控制項（例如按鈕、滑桿），一樣排成一排。區塊的名字就是標題（輔助技術唸 Grouping）。
Column {
    id: group

    property string title: ""
    property string hint: ""
    default property alias options: optionRow.data

    width: parent ? parent.width : 0
    topPadding: Theme.spaceL
    bottomPadding: Theme.spaceL
    spacing: 12
    Accessible.role: Accessible.Grouping
    Accessible.name: title

    Text {
        bottomPadding: 4
        text: group.title
        color: Theme.textTitle
        font.pixelSize: Theme.fontBodyLarge
        font.weight: Font.Bold
        Accessible.ignored: true    // 區塊自己就叫這個名字
    }

    Row {
        id: optionRow
        width: group.width
        spacing: Theme.spaceS
    }

    Text {
        objectName: "groupHint"
        width: group.width
        visible: text !== ""
        text: group.hint
        color: Theme.textMuted
        font.pixelSize: Theme.fontSmall
        lineHeight: 18
        lineHeightMode: Text.FixedHeight
        wrapMode: Text.Wrap
    }
}
