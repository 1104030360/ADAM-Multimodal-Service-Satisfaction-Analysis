import QtQuick

// 服務明細（服務人員端）。畫面和行為在 ServiceDetailView.qml，兩端共用（UX-46）；這裡只決定 Back 回到哪一頁。
ServiceDetailView {
    backPage: "../../qml/pages/server_service_page.qml"
}
