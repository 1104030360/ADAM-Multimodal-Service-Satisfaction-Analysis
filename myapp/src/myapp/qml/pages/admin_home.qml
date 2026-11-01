import QtQuick
import "../components"

Window {
    id: window
    visible: true
    width: 1920
    height: 1080
    title: qsTr("Admin Home")
    visibility: "FullScreen"
    color: Theme.contentBg  // 換頁時新頁面還沒建好的那幾個畫格，內容區是內容頁的底色

    // 減少動態效果（ENG-18、DS-43）：設定頁的選項、系統設定、測試的 animationScale 都經由 Theme 生效。
    // uiPrefs 由 MainWindow（或 launch()）註冊；是 null 時不寫，Theme 用自己的預設值
    Binding {
        target: Theme
        property: "motionMode"
        when: !!uiPrefs
        value: uiPrefs ? uiPrefs.motionMode : 0
    }

    Binding {
        target: Theme
        property: "animationScale"
        when: !!uiPrefs
        value: uiPrefs ? uiPrefs.animationScale : 1
    }

    // ── 換頁（DS-29、ENG-19、T23）：和服務人員端的外殼（server_home.qml）同一套做法
    // 內容區現在的頁面（檔名）：側邊欄的「目前頁面」照它畫。側邊欄、⌘1～⌘5 在要求換頁的當下就改；
    // 頁面自己換頁（Details、Back）則跟著 Loader 的 source 改
    property string currentPage: ""
    property bool shellCompleted: false     // 第一頁和外殼一起建好之後才算「換頁」：淡入、唸頁名
    property var announced: []              // 唸過的頁名（Accessible.announce 在測試裡看不到，T17 的做法）

    function fileName(url) {
        const path = url.toString()
        return path.slice(path.lastIndexOf("/") + 1)
    }

    // 側邊欄、⌘1～⌘5：在背景載入、分好幾個畫格建立（Loader.asynchronous，ENG-19），GUI 執行緒不會卡住。
    // 舊頁面馬上拿掉；同一頁再按一次不重新載入。頁面自己換頁照舊同步（T23）
    function showPage(file) {
        if (currentPage === file)
            return
        currentPage = file
        pageFade.stop()     // 舊頁面還在的時候停：淡入的對象等一下就會被刪掉
        mainContentLoader.asynchronous = true
        mainContentLoader.source = Qt.resolvedUrl(file)
    }

    // ⌘1～⌘5：和按側邊欄的按鈕一樣，焦點也移到那個按鈕上（DS-29：換頁後焦點在側邊欄）
    function activateNav(button) {
        button.forceActiveFocus(Qt.ShortcutFocusReason)
        button.click()
    }

    // 換頁時唸出的頁名（DS-29、DS-51）。明細頁在側邊欄算 Services
    function pageName(file) {
        switch (file) {
        case "admin_home_page.qml": return homeButton.text
        case "admin_add_page.qml": return addAccountButton.text
        case "admin_service_page.qml": return servicesButton.text
        case "admin_service_detail_page.qml": return qsTr("Service Details")
        case "admin_reports_page.qml": return reportsButton.text
        case "admin_setting_page.qml": return settingsButton.text
        }
        return ""
    }

    Rectangle {
        id: sidebar
        width: 288
        height: parent.height
        color: Theme.sidebarBg

        // 側邊欄的按鈕是真的 Button（SidebarButton）：Tab 照這個順序停，Space、Return 按下（UX-41、DS-32）。
        // 左右各留 spaceS，鍵盤焦點框（往外 4 px）才不會被視窗邊緣或內容區切掉
        Column {
            id: sidebarContent
            anchors.fill: parent
            anchors.topMargin: Theme.space4XL
            anchors.leftMargin: Theme.spaceS
            anchors.rightMargin: Theme.spaceS
            spacing: Theme.spaceL

            Image {
                id: avatar
                source: "../../images/icons/user.png"
                width: 100
                height: 100
                anchors.horizontalCenter: parent.horizontalCenter
                fillMode: Image.PreserveAspectFit
                Accessible.ignored: true
            }

            Text {
                id: username
                // 帳號名稱在背景載入（ENG-06）：載入前是空的，不在 binding 裡同步查資料庫。
                // 還沒載入時也先留一行，下面的按鈕不會跳（Column 會跳過寬或高是 0 的項目）
                text: adminHome ? adminHome.profile.name : ""
                width: parent.width
                height: 30
                elide: Text.ElideRight
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
                color: Theme.textOnDark
                font.pixelSize: Theme.fontHeadline
                font.bold: true
            }

            SidebarButton {
                id: homeButton
                objectName: "homeButton"
                width: parent.width
                text: qsTr("Home")
                iconName: "house"
                current: window.currentPage === "admin_home_page.qml"
                onClicked: window.showPage("admin_home_page.qml")
            }

            SidebarButton {
                id: addAccountButton
                objectName: "addAccountButton"
                width: parent.width
                text: qsTr("Add Account")
                iconName: "user-round-plus"
                current: window.currentPage === "admin_add_page.qml"
                // 不再順便載入首頁的資料：新增帳號頁用不到（UX-32）
                onClicked: window.showPage("admin_add_page.qml")
            }

            SidebarButton {
                id: servicesButton
                objectName: "servicesButton"
                width: parent.width
                text: qsTr("Services")
                iconName: "history"
                current: window.currentPage === "admin_service_page.qml"
                         || window.currentPage === "admin_service_detail_page.qml"
                onClicked: window.showPage("admin_service_page.qml")
            }

            SidebarButton {
                id: reportsButton
                objectName: "reportsButton"
                width: parent.width
                text: qsTr("Reports")
                iconName: "file-chart-column"
                current: window.currentPage === "admin_reports_page.qml"
                onClicked: window.showPage("admin_reports_page.qml")
            }

            SidebarButton {
                id: settingsButton
                objectName: "settingsButton"
                width: parent.width
                text: qsTr("Settings")
                iconName: "settings"
                current: window.currentPage === "admin_setting_page.qml"
                onClicked: window.showPage("admin_setting_page.qml")
            }

            // Quit：管理員端沒有錄影和上傳，不必問，清掉快取就結束
            SidebarButton {
                id: exitButton
                objectName: "exitButton"
                width: parent.width
                text: qsTr("Quit")
                iconName: "log-out"
                onClicked: {
                    adminHome.clearDir()
                    Qt.quit() // 退出應用程式
                }
            }
        }

        // ⌘1～⌘5：照側邊欄的順序換頁（UX-41）。Qt 在 macOS 上的 Ctrl 就是 ⌘。管理員端只有 5 頁，⌘6 沒有作用。
        // 放在側邊欄裡、不直接放在 Window 底下：模態對話框開著時就不會觸發
        Shortcut { sequence: "Ctrl+1"; onActivated: window.activateNav(homeButton) }
        Shortcut { sequence: "Ctrl+2"; onActivated: window.activateNav(addAccountButton) }
        Shortcut { sequence: "Ctrl+3"; onActivated: window.activateNav(servicesButton) }
        Shortcut { sequence: "Ctrl+4"; onActivated: window.activateNav(reportsButton) }
        Shortcut { sequence: "Ctrl+5"; onActivated: window.activateNav(settingsButton) }
    }

    // 第一頁和外殼一起同步建好：launch() 回傳時首頁已經在，視窗一出現就有內容（T19 的測試靠這點）。
    // 之後側邊欄的換頁才在背景載入（showPage 把 asynchronous 打開，載完就關掉）。Loader 是 focus scope，
    // 新頁面裡設了 focus 的項目不會把焦點從側邊欄搶走（DS-29）
    Loader {
        id: mainContentLoader
        objectName: "mainContentLoader"
        anchors.left: sidebar.right
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        source: "admin_home_page.qml" // 初始顯示的內容

        onSourceChanged: window.currentPage = window.fileName(source)
        onStatusChanged: {
            if (status !== Loader.Loading)
                asynchronous = false
        }
        onLoaded: {
            if (item && typeof item.setWindow === "function") {
                item.setWindow(window)
            }
            if (!window.shellCompleted)
                return
            // 換頁（DS-29、D10）：新頁面淡入，並用 Polite 唸出頁名；焦點不動，留在側邊欄
            pageFade.stop()
            if (Theme.durBase > 0) {
                pageFade.target = item
                pageFade.start()
            }
            const name = window.pageName(window.currentPage)
            if (name !== "") {
                window.announced = window.announced.concat([name])
                Accessible.announce(name, Accessible.Polite)
            }
        }
    }

    // M-5 換頁：新頁面 opacity 0→1、150 ms、OutCubic；不滑動，也不和舊頁交疊（舊頁在換頁的當下就拿掉了）。
    // OpacityAnimator 在 render thread 上跑（ENG-19），結束時才把 1 寫回頁面。時間是 0（減少動態、
    // 測試的 animationScale 0）時不跑：Animator 的時間是 0 會停在起點，頁面會一直是透明的（第二波的教訓）
    OpacityAnimator {
        id: pageFade
        objectName: "pageFade"
        from: 0
        to: 1
        duration: Theme.durBase
        easing.type: Theme.easeEnter
    }

    // 通知堆疊（DS-21）：內容區的右下角、距邊 24，蓋在頁面和對話框上面。
    // 延伸到內容區底部的清單要設 bottomMargin: toastHost.occupiedHeight，最後一列才捲得出來
    ToastHost {
        id: toastHost
        objectName: "toastHost"
        center: notifier
        x: window.width - width - Theme.spaceXL
        y: window.height - height - Theme.spaceXL
    }

    // 舊的 id：其他測試檔（例如 test_admin.py）還用以前 MouseArea 的 id 找側邊欄的按鈕。
    // 那些測試改用上面的 id（例如 addAccountButton）之後就刪掉這一段（T23）
    property alias mouseArea: homeButton
    property alias mouseArea2: addAccountButton
    property alias mouseArea3: servicesButton
    property alias mouseArea4: reportsButton
    property alias mouseArea5: settingsButton
    property alias mouseArea6: exitButton

    Component.onCompleted: shellCompleted = true
}
