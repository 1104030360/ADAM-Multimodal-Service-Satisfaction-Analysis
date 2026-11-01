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

    // 要馬上拿到新頁面時：同步載入。側邊欄的換頁如果還在背景載入，先讓它立刻完成，再換（和服務人員端一樣）
    function showPageNow(file) {
        pageFade.stop()
        mainContentLoader.asynchronous = false
        mainContentLoader.source = Qt.resolvedUrl(file)
        return mainContentLoader.item
    }

    // 打開一筆服務的明細：Services 的 Details、首頁的長條、搜尋都走這裡。
    // 先換頁（同步，回傳明細頁），檔案在背景查詢、下載（UX-22、ENG-04）
    function openServiceDetail(serviceId, startTime) {
        adminHome.load_service_media(serviceId, startTime || "")
        return showPageNow("admin_service_detail_page.qml")
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

    // 頁首在標題下面的一句說明：這一頁做什麼（PLAN 6-1）。首頁是字標，沒有說明
    function pageSubtitle(file) {
        switch (file) {
        case "admin_add_page.qml": return qsTr("Add a staff or admin account.")
        case "admin_service_page.qml": return qsTr("Find any staff member's services and open their videos and charts.")
        case "admin_reports_page.qml": return qsTr("Choose a report to read it here.")
        case "admin_setting_page.qml": return qsTr("Changes apply right away and are kept for next time.")
        }
        return ""
    }

    // ── 側邊欄（PLAN 第 4 節）：寬 96，圖示在上、字在下；名字和頭像移到頁首，Quit 在最下面
    Rectangle {
        id: sidebar
        width: Theme.railWidth
        height: parent.height
        color: Theme.railBg

        Rectangle {     // 右邊的線
            anchors.right: parent.right
            width: 1
            height: parent.height
            color: Theme.railBorder
        }

        // 側邊欄的按鈕是真的 Button（SidebarButton）：Tab 照這個順序停，Space、Return 按下（UX-41、DS-32）
        Column {
            id: sidebarContent
            anchors.top: parent.top
            anchors.topMargin: Theme.spaceL
            anchors.horizontalCenter: parent.horizontalCenter
            spacing: 6

            SidebarButton {
                id: homeButton
                objectName: "homeButton"
                text: qsTr("Home")
                iconName: "house"
                current: window.currentPage === "admin_home_page.qml"
                onClicked: window.showPage("admin_home_page.qml")
            }

            SidebarButton {
                id: addAccountButton
                objectName: "addAccountButton"
                text: qsTr("Add Account")
                iconName: "user-round-plus"
                current: window.currentPage === "admin_add_page.qml"
                // 不再順便載入首頁的資料：新增帳號頁用不到（UX-32）
                onClicked: window.showPage("admin_add_page.qml")
            }

            SidebarButton {
                id: servicesButton
                objectName: "servicesButton"
                text: qsTr("Services")
                iconName: "history"
                current: window.currentPage === "admin_service_page.qml"
                         || window.currentPage === "admin_service_detail_page.qml"
                onClicked: window.showPage("admin_service_page.qml")
            }

            SidebarButton {
                id: reportsButton
                objectName: "reportsButton"
                text: qsTr("Reports")
                iconName: "file-chart-column"
                current: window.currentPage === "admin_reports_page.qml"
                onClicked: window.showPage("admin_reports_page.qml")
            }

            SidebarButton {
                id: settingsButton
                objectName: "settingsButton"
                text: qsTr("Settings")
                iconName: "settings"
                current: window.currentPage === "admin_setting_page.qml"
                onClicked: window.showPage("admin_setting_page.qml")
            }
        }

        // Quit：管理員端沒有錄影和上傳，不必問，清掉快取就結束
        SidebarButton {
            id: exitButton
            objectName: "exitButton"
            anchors.bottom: parent.bottom
            anchors.bottomMargin: Theme.spaceL
            anchors.horizontalCenter: parent.horizontalCenter
            text: qsTr("Quit")
            iconName: "log-out"
            onClicked: {
                adminHome.clearDir()
                Qt.quit() // 退出應用程式
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
    // ── 內容區（PLAN 第 4 節、6-1）：頁首和頁面在同一欄，最寬 1360，螢幕更寬時置中。頁面自己處理捲動
    Item {
        id: contentArea
        anchors.left: sidebar.right
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        anchors.leftMargin: Theme.pagePaddingH
        anchors.rightMargin: Theme.pagePaddingH
        anchors.topMargin: Theme.pagePaddingTop
        anchors.bottomMargin: Theme.pagePaddingBottom
    }

    // 頁面可以自己給頁首的內容（明細頁的服務 ID、‹ Services）：頁面有 headerTitle 之類的屬性就用頁面的
    function pageValue(name, fallback) {
        const page = mainContentLoader.item
        return page && page[name] !== undefined ? page[name] : fallback
    }

    PageHeader {
        id: header
        objectName: "pageHeader"
        x: contentArea.x + (contentArea.width - width) / 2
        y: contentArea.y
        width: Math.min(Theme.contentMaxWidth, contentArea.width)
        wordmark: window.currentPage === "admin_home_page.qml"
        title: window.pageValue("headerTitle", window.pageName(window.currentPage))
        subtitle: window.pageValue("headerSubtitle", window.pageSubtitle(window.currentPage))
        backText: window.pageValue("headerBackText", "")
        onBack: mainContentLoader.item.goBack()
        actionText: wordmark ? qsTr("Add Account") : ""     // 首頁唯一的主要動作（PLAN 5-1、UD12）
        actionIcon: "../components/icons/user-round-plus_actionPrimaryText.svg"
        onActionClicked: window.activateNav(addAccountButton)
        // 帳號名稱、等級在背景載入（ENG-06）：載入前是空的、0，不在 binding 裡同步查資料庫
        userName: adminHome ? adminHome.profile.name : ""
        userRole: adminHome && adminHome.profile.level > 0 ? qsTr("Admin · level %1").arg(adminHome.profile.level)
                                                           : qsTr("Admin")
        // 搜尋服務 ID（UD5、T32）：放在標題和主要動作之間；明細頁沒有（設計稿的明細頁頁首是「‹ Services」和服務 ID）
        ServiceSearchField {
            objectName: "serviceSearchField"
            search: serviceSearch
            wide: header.wordmark
            visible: window.currentPage !== "admin_service_detail_page.qml"
            onChosen: (serviceId, startTime) => window.openServiceDetail(serviceId, startTime)
        }
    }

    Loader {
        id: mainContentLoader
        objectName: "mainContentLoader"
        x: header.x
        y: header.y + header.height + Theme.sectionGap
        width: header.width
        height: contentArea.y + contentArea.height - y
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

    Component.onCompleted: shellCompleted = true
}
