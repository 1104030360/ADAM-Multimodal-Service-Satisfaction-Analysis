import QtQuick

// 服務明細（管理員端）。畫面和行為在 ServiceDetailView.qml，兩端共用（UX-46）；這裡只決定頁首的「‹ Services」回到哪一頁，
// 以及頁首的說明前面加上服務人員的名字（管理員看的是任何人的服務）。
ServiceDetailView {
    backPage: "admin_service_page.qml"
    showStaffName: true
}
