import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

Item {
    width: 400
    height: 50  // 调整高度以适应单行显示

    Row {
        anchors.centerIn: parent
        spacing: 20

        Text {
            text: "視窗控制選項"
            font.pixelSize: 24
        }

        // 兩個勾選框都直接看主視窗現在是不是全螢幕，視窗模式一變就會跟著更新
        CheckBox {
            id: exitFullscreenCheckbox
            width: 150
            text: "視窗模式"
            ButtonGroup.group: windowModeGroup
            checked: window ? window.visibility !== Window.FullScreen : false
            onClicked: {
                if (window) {
                    window.showNormal() // 退出全屏模式
                }
            }
        }

        CheckBox {
            id: fullscreenCheckbox
            width: 150
            text: "全螢幕模式"
            ButtonGroup.group: windowModeGroup
            checked: window ? window.visibility === Window.FullScreen : true // 主視窗一打開就是全螢幕
            onClicked: {
                if (window) {
                    window.showFullScreen() // 恢复全屏模式
                }
            }
        }
    }

    // 兩個選項互斥：同一組只會勾一個，已經勾選的那個再按也不會被取消
    ButtonGroup {
        id: windowModeGroup
    }

    property var window

    function setWindow(win) {
        window = win
    }
}
