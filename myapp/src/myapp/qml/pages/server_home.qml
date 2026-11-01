import QtQuick
import "../components"

Window {
    id: window
    visible: true
    width: 1920
    height: 1080
    title: qsTr("Staff Home")
    // 視窗模式（全螢幕或視窗）由 launch() 照這個帳號上次的模式決定（new-feature PLAN 13.2 S1）
    color: Theme.contentBg  // 換頁時新頁面還沒建好的那幾個畫格，內容區是內容頁的底色

    // 關掉主視窗（紅色按鈕、⌘W）和按 Quit 一樣：一律先問（N1）；錄影中先停止並存檔，還有錄影在存時問上傳的警告。
    // ⌘Q 不會經過這裡（app 是 QApplication，結束時只問 QWidget 視窗；T20）：外殼開著時 serverHome.session 向
    // QuitGuard 登記（錄影中、存檔中時 deviceFinder 也登記），攔下來之後從 quitRequested 走同一條路
    onClosing: (close) => { if (!exitButton.requestQuit()) close.accepted = false }

    // 鍵盤（Tab、⌘1～⌘6）走到側邊欄裡看不到的按鈕：捲過去（視窗太矮時側邊欄可以捲動）
    onActiveFocusItemChanged: if (railScroller) railScroller.reveal(activeFocusItem)

    // 已經有一個離開的對話框開著：不再開第二個（⌘Q 可能同時被兩個登記擋下來，N1 的「不疊兩層」）
    function leaving() {
        return quitDialog.visible || quitConfirmDialog.visible || logoutDialog.visible
    }

    // 動態設定寫進 Theme 單例（ENG-18）；程式結束時 context property 會先被刪掉，要先判斷是否為 null
    Binding { target: Theme; property: "motionMode"; value: uiPrefs ? uiPrefs.motionMode : 0 }
    Binding { target: Theme; property: "animationScale"; value: uiPrefs ? uiPrefs.animationScale : 1 }

    // ── 換頁（DS-29、ENG-19、T23）
    // 內容區現在的頁面（檔名）：側邊欄的「目前頁面」照它畫。側邊欄、⌘1～⌘6 在要求換頁的當下就改；
    // 頁面自己換頁（Details、Back）、通知的 View Results 則跟著 Loader 的 source 改
    property string currentPage: ""
    property bool shellCompleted: false     // 第一頁和外殼一起建好之後才算「換頁」：淡入、唸頁名
    property var announced: []              // 唸過的頁名（Accessible.announce 在測試裡看不到，T17 的做法）

    function fileName(url) {
        const path = url.toString()
        return path.slice(path.lastIndexOf("/") + 1)
    }

    // 側邊欄、⌘1～⌘6：在背景載入、分好幾個畫格建立（Loader.asynchronous，ENG-19），GUI 執行緒不會卡住。
    // 舊頁面馬上拿掉；同一頁再按一次不重新載入。頁面自己換頁和 View Results 照舊同步（T23）
    function showPage(file) {
        if (currentPage === file)
            return
        currentPage = file
        pageFade.stop()     // 舊頁面還在的時候停：淡入的對象等一下就會被刪掉
        mainContentLoader.asynchronous = true
        mainContentLoader.source = Qt.resolvedUrl(file)
    }

    // 要馬上拿到新頁面時（View Results 要標出那一列）：同步載入。側邊欄的換頁如果還在背景載入，
    // 先讓它立刻完成（Loader.asynchronous 改成 false 就會，Qt 文件寫的用法），再換
    function showPageNow(file) {
        pageFade.stop()
        mainContentLoader.asynchronous = false
        mainContentLoader.source = Qt.resolvedUrl(file)
        return mainContentLoader.item
    }

    // 打開一筆服務的明細：Services 的 Details、分析頁的 View、首頁的長條、搜尋都走這裡。
    // 先換頁（同步，回傳明細頁），檔案在背景查詢、下載（UX-22、ENG-04）
    function openServiceDetail(serviceId, startTime) {
        serverHome.load_service_media(serviceId, startTime || "")
        return showPageNow("server_service_detail_page.qml")
    }

    // 打開一份報表（new-feature N4，T37）：分析頁的 Report、通知的 View 都走這裡。先選好再換頁（同步，回傳報表頁）
    function openReport(reportId) {
        reportsModel.select(reportId)
        return showPageNow("server_reports_page.qml")
    }

    // ⌘1～⌘6：和按側邊欄的按鈕一樣，焦點也移到那個按鈕上（DS-29：換頁後焦點在側邊欄）
    function activateNav(button) {
        button.forceActiveFocus(Qt.ShortcutFocusReason)
        button.click()
    }

    // 換頁時唸出的頁名（DS-29、DS-51）。明細頁在側邊欄算 Services
    function pageName(file) {
        switch (file) {
        case "server_home_button.qml": return homeButton.text
        case "record_widget.qml": return recordButton.text
        case "analysis.qml": return analysisButton.text
        case "server_service_page.qml": return servicesButton.text
        case "server_service_detail_page.qml": return qsTr("Service Details")
        case "server_reports_page.qml": return reportsButton.text
        case "server_setting_button.qml": return settingsButton.text
        }
        return ""
    }

    // 頁首在標題下面的一句說明：這一頁做什麼（PLAN 6-1）。首頁是字標，沒有說明
    function pageSubtitle(file) {
        switch (file) {
        case "record_widget.qml": return qsTr("Choose the two cameras and the microphone, then open the recorder.")
        case "analysis.qml": return qsTr("Send recorded services for analysis and see the results.")
        case "server_service_page.qml": return qsTr("Find a past service and open its videos and charts.")
        case "server_reports_page.qml": return qsTr("Reports an admin has published for you.")
        case "server_setting_button.qml": return qsTr("Preferences apply right away. Account and device changes apply when you save.")
        }
        return ""
    }

    // ── 側邊欄（PLAN 第 4 節）：寬 96，圖示在上、字在下；名字和頭像移到頁首，Log Out、Quit 在最下面（N1）
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

        // 視窗太矮、放不下全部的按鈕時（例如小螢幕上的 Windowed），整條側邊欄可以捲動；平常不能拖。
        // 鍵盤走到看不到的按鈕時捲過去（window 的 onActiveFocusItemChanged）
        Flickable {
            id: railScroller
            objectName: "railScroller"
            anchors.fill: parent
            contentWidth: width
            contentHeight: Math.max(height, sidebarContent.y + sidebarContent.height + sessionButtons.height
                                            + 2 * Theme.spaceL)
            interactive: contentHeight > height
            boundsBehavior: Flickable.StopAtBounds
            clip: true

            // 側邊欄裡的 item 有一部分在看不到的地方：捲到整個看得到
            function reveal(item) {
                let inside = item
                while (inside && inside !== contentItem)
                    inside = inside.parent
                if (!inside || !interactive)
                    return
                const top = item.mapToItem(contentItem, 0, 0).y
                if (top < contentY)
                    contentY = Math.max(0, top - Theme.spaceL)
                else if (top + item.height > contentY + height)
                    contentY = Math.min(contentHeight - height, top + item.height - height + Theme.spaceL)
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
                    current: window.currentPage === "server_home_button.qml"
                    onClicked: {
                        window.showPage("server_home_button.qml")
                        serverHome.onLoadHome()  // 在背景重新載入；舊的分數留著、變淡
                    }
                }

                SidebarButton {
                    id: recordButton
                    objectName: "recordButton"
                    text: qsTr("Record")
                    iconName: "video"
                    current: window.currentPage === "record_widget.qml"
                    onClicked: window.showPage("record_widget.qml")
                }

                SidebarButton {
                    id: analysisButton
                    objectName: "analysisButton"
                    text: qsTr("Analysis")
                    iconName: "brain-circuit"
                    current: window.currentPage === "analysis.qml"
                    onClicked: {
                        window.showPage("analysis.qml")  // 先換頁
                        serverHome.onLoadAnalysis()  // 清單在背景重新整理（1 個往返）
                    }
                }

                SidebarButton {
                    id: servicesButton
                    objectName: "servicesButton"
                    text: qsTr("Services")
                    iconName: "history"
                    current: window.currentPage === "server_service_page.qml"
                             || window.currentPage === "server_service_detail_page.qml"
                    onClicked: window.showPage("server_service_page.qml")
                }

                SidebarButton {
                    id: reportsButton
                    objectName: "reportsButton"
                    text: qsTr("Reports")
                    iconName: "file-chart-column"
                    current: window.currentPage === "server_reports_page.qml"
                    onClicked: window.showPage("server_reports_page.qml")
                }

                SidebarButton {
                    id: settingsButton
                    objectName: "settingsButton"
                    text: qsTr("Settings")
                    iconName: "settings"
                    current: window.currentPage === "server_setting_button.qml"
                    onClicked: window.showPage("server_setting_button.qml")
                }
            }

            // ── 最下面：Log Out、Quit（N1）。兩個都先問；錄影中先停止並存檔（UX-09），還有錄影在收尾、上傳或寫資料庫時
            // 改問上傳的警告（預設 Keep Uploading），不疊兩層對話框。視窗太矮、放不下兩組按鈕時，接在上面那一組的下面，
            // 不會疊在一起（側邊欄可以捲動）
            Column {
                id: sessionButtons
                anchors.horizontalCenter: parent.horizontalCenter
                y: Math.max(sidebarContent.y + sidebarContent.height + Theme.spaceL,
                            railScroller.height - height - Theme.spaceL)
                spacing: 6

                // Log Out：問過之後回到登入視窗（serverHome.logOut()：清快取、停掉這個帳號的工作、關掉外殼）
                SidebarButton {
                    id: logoutButton
                    objectName: "logoutButton"
                    text: qsTr("Log Out")
                    iconName: "log-out"

                    onClicked: logoutButton.requestLogOut()

                    function requestLogOut() {
                        if (exitButton.quitting || window.leaving())
                            return
                        const finder = exitButton.finder
                        if (finder)
                            finder.stopRecording()
                        window.raise()      // 錄影視窗在前面時，對話框在主視窗上
                        window.requestActivate()
                        if (finder && finder.uploadCount > 0)
                            quitDialog.ask("logOut")
                        else
                            logoutDialog.open()
                    }

                    function logOutNow() {
                        serverHome.logOut()
                    }

                    ConfirmDialog {
                        id: logoutDialog
                        objectName: "logoutDialog"
                        title: qsTr("Log out?")
                        body: qsTr("You'll return to the sign-in screen.")
                        confirmText: qsTr("Log Out")
                        cancelText: qsTr("Stay Logged In")
                        confirmVariant: "secondary"     // 沒有東西會丟：不用紅色
                        onConfirmed: logoutButton.logOutNow()
                    }
                }

                // Quit：視窗的 onClosing、⌘W、⌘Q（quitRequested）也走這裡
                SidebarButton {
                    id: exitButton
                    objectName: "exitButton"
                    text: qsTr("Quit")
                    iconName: "power"

                    readonly property var finder: typeof deviceFinder !== "undefined" ? deviceFinder : null
                    readonly property var session: serverHome ? serverHome.session : null
                    property bool quitting: false

                    onClicked: exitButton.requestQuit()

                    // 回傳 true 表示可以結束（onClosing 用）；其他時候先問，回傳 false
                    function requestQuit() {
                        if (quitting)
                            return true
                        if (window.leaving())
                            return false
                        if (finder)
                            finder.stopRecording()
                        window.raise()      // 在錄影視窗按 ⌘Q 時，對話框在主視窗上
                        window.requestActivate()
                        if (finder && finder.uploadCount > 0)
                            quitDialog.ask("quit")
                        else
                            quitConfirmDialog.open()
                        return false
                    }

                    function quitNow() {
                        quitting = true
                        if (finder)
                            finder.allowQuit()  // 接下來的 QEvent.Quit 不再攔
                        if (session)
                            session.allowQuit()
                        serverHome.clearDir()
                        Qt.quit() // 退出應用程式
                    }

                    Connections {
                        target: exitButton.finder
                        function onQuitRequested() { exitButton.requestQuit() }
                        function onUploadsChanged() {
                            // 問的時候都存好（或失敗）了：沒有東西會丟，不必再問；要離開再按一次
                            if (quitDialog.opened && exitButton.finder.uploadCount === 0)
                                quitDialog.close()
                        }
                    }

                    Connections {
                        target: exitButton.session
                        function onQuitRequested() { exitButton.requestQuit() }
                    }

                    ConfirmDialog {
                        id: quitConfirmDialog
                        objectName: "quitConfirmDialog"
                        title: qsTr("Quit ADAM?")
                        confirmText: qsTr("Quit")
                        cancelText: qsTr("Don't Quit")
                        confirmVariant: "secondary"     // 沒有東西會丟：不用紅色
                        onConfirmed: exitButton.quitNow()
                    }

                    // 上傳中按 Quit 或 Log Out：說會丟掉哪幾段（DS-28）。purpose 是按的是哪一個
                    ConfirmDialog {
                        id: quitDialog
                        objectName: "quitDialog"
                        property string purpose: "quit"     // quit、logOut
                        readonly property bool loggingOut: purpose === "logOut"
                        readonly property var finder: exitButton.finder
                        readonly property int count: finder ? finder.uploadCount : 0
                        readonly property real total: finder ? finder.uploadTotal : 0
                        // 「 (58.6 / 124.0 MB)」；還不知道大小（檔案還在寫）時不寫
                        readonly property string amount: total > 0
                            ? qsTr(" (%1 / %2 MB)").arg((Math.min(finder.uploadDone, total) / 1e6).toFixed(1))
                                                   .arg((total / 1e6).toFixed(1))
                            : ""
                        title: loggingOut ? qsTr("Log out while uploading?") : qsTr("Quit while uploading?")
                        body: count > 1
                            ? (loggingOut ? qsTr("%1 services are still uploading%2. If you log out now, they won't be saved.")
                                          : qsTr("%1 services are still uploading%2. If you quit now, they won't be saved."))
                                  .arg(count).arg(amount)
                            : (loggingOut ? qsTr("%1 is still uploading%2. If you log out now, this service won't be saved.")
                                          : qsTr("%1 is still uploading%2. If you quit now, this service won't be saved."))
                                  .arg(finder ? finder.uploadServiceId : "").arg(amount)
                        confirmText: loggingOut ? qsTr("Log Out Anyway") : qsTr("Quit Anyway")
                        cancelText: qsTr("Keep Uploading")
                        onConfirmed: loggingOut ? logoutButton.logOutNow() : exitButton.quitNow()

                        function ask(why) {
                            purpose = why
                            open()
                        }
                    }
                }
            }
        }

        // ⌘1～⌘6：照側邊欄的順序換頁（UX-41）。Qt 在 macOS 上的 Ctrl 就是 ⌘。
        // 放在側邊欄裡、不直接放在 Window 底下：模態對話框（Quit while uploading? 等）開著時就不會觸發
        Shortcut { sequence: "Ctrl+1"; onActivated: window.activateNav(homeButton) }
        Shortcut { sequence: "Ctrl+2"; onActivated: window.activateNav(recordButton) }
        Shortcut { sequence: "Ctrl+3"; onActivated: window.activateNav(analysisButton) }
        Shortcut { sequence: "Ctrl+4"; onActivated: window.activateNav(servicesButton) }
        Shortcut { sequence: "Ctrl+5"; onActivated: window.activateNav(reportsButton) }
        Shortcut { sequence: "Ctrl+6"; onActivated: window.activateNav(settingsButton) }
        // ⌘W：關掉主視窗，和紅色按鈕一樣走 onClosing（N1）
        Shortcut { sequence: "Ctrl+W"; onActivated: window.close() }
    }

    // 第一頁和外殼一起同步建好：launch() 回傳時首頁已經在，視窗一出現就有內容（T18、T19 的測試靠這點）。
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
        wordmark: window.currentPage === "server_home_button.qml"
        title: window.pageValue("headerTitle", window.pageName(window.currentPage))
        subtitle: window.pageValue("headerSubtitle", window.pageSubtitle(window.currentPage))
        backText: window.pageValue("headerBackText", "")
        onBack: mainContentLoader.item.goBack()
        actionText: wordmark ? qsTr("New Recording") : ""    // 首頁唯一的主要動作（PLAN 5-1、UD12）
        actionIcon: "../components/icons/video_actionPrimaryText.svg"
        onActionClicked: window.activateNav(recordButton)
        userName: serverHome ? serverHome.accountName : ""  // 在 db lane 載入，到了就更新
        userRole: qsTr("Staff")
        userPhoto: serverHome ? serverHome.profile.photoSource : ""    // 設定頁換的照片（T36）
        // 搜尋服務 ID（UD5、T32）：放在標題和主要動作之間；明細頁沒有（設計稿的明細頁頁首是「‹ Services」和服務 ID）
        ServiceSearchField {
            objectName: "serviceSearchField"
            search: serviceSearch
            wide: header.wordmark
            visible: window.currentPage !== "server_service_detail_page.qml"
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
        source: "server_home_button.qml" // 初始顯示的內容

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

    // 通知上的「View Results」（open-analysis:<sid>）：換到分析頁、選 Completed、標出那一列（UX-17、ENG-15）
    Connections {
        target: serverHome
        function onOpenAnalysisRequested(serviceId) {
            const page = window.showPageNow("analysis.qml")
            if (page && typeof page.showService === "function")
                page.showService(serviceId)
        }
    }

    // 通知上的「View」（open-report:<report_id>）：換到報表頁、選那一份（new-feature N4）
    Connections {
        target: reportsModel
        function onOpenRequested(reportId) { window.openReport(reportId) }
    }

    // 通知堆疊：內容區右下角，距邊 24（DS-21）。延伸到底部的清單用 toastHost.occupiedHeight 當 bottomMargin
    ToastHost {
        id: toastHost
        objectName: "toastHost"
        center: notifier
        x: window.width - width - Theme.spaceXL
        y: window.height - height - Theme.spaceXL
    }

    Component.onCompleted: shellCompleted = true
}
