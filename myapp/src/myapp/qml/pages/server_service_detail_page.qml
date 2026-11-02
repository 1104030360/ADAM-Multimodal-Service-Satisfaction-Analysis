import QtQuick

// 服務明細（服務人員端）。畫面和行為在 ServiceDetailView.qml，兩端共用（UX-46）；這裡只決定頁首的「‹ Services」回到哪一頁。
ServiceDetailView {
    backPage: "server_service_page.qml"
}
