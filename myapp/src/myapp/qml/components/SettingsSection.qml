import QtQuick

// 設定頁的一個區塊（new-feature PLAN 13.1）：一張卡片，上面是區塊名稱（Account、General、Devices、Help），
// 下面是這個區塊的幾組設定（SettingsGroup），組和組之間用 SettingsDivider 隔開。
// 卡片的上下內距比一般的卡片小：每一組自己有上下的空白。
Card {
    id: section

    property string title: ""
    default property alias content: body.data

    width: parent ? parent.width : 0
    topPadding: Theme.spaceL
    bottomPadding: 8
    Accessible.role: Accessible.Grouping
    Accessible.name: title

    Column {
        width: section.availableWidth

        Text {
            objectName: "sectionTitle"
            text: section.title
            color: Theme.textTitle
            font.pixelSize: Theme.fontTitleSmall
            font.weight: Font.Bold
            Accessible.ignored: true    // 區塊自己就叫這個名字
        }

        Column {
            id: body
            width: parent.width
        }
    }
}
