pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "."

AppDialog {
    id: root
    property var controller: AppController
    readonly property bool compactLayout: root.parent && root.parent.height < 800
    readonly property int qrSize: compactLayout ? 136 : 160

    title: qsTr("Giới thiệu")
    subtitle: qsTr("LinSub · Phiên bản %1").arg(root.controller.currentAppVersion)
    preferredWidth: 650
    maximumWidth: 680
    maximumHeight: 860
    bodySpacing: Theme.space12

    ScrollView {
        id: aboutScroll
        Layout.fillWidth: true
        Layout.fillHeight: true
        Layout.minimumHeight: 0
        implicitHeight: aboutContent.implicitHeight
        contentWidth: availableWidth
        contentHeight: aboutContent.implicitHeight
        clip: true
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
        ScrollBar.vertical.policy: ScrollBar.AlwaysOff

        ColumnLayout {
            id: aboutContent
            width: aboutScroll.availableWidth
            spacing: root.compactLayout ? Theme.space8 : Theme.space12

            RowLayout {
                Layout.fillWidth: true
                spacing: Theme.space16

                Image {
                    Layout.preferredWidth: root.compactLayout ? 56 : 68
                    Layout.preferredHeight: root.compactLayout ? 56 : 68
                    source: Qt.resolvedUrl("../assets/branding/linsub-mark.png")
                    sourceSize.width: 176
                    sourceSize.height: 176
                    fillMode: Image.PreserveAspectFit
                    asynchronous: true
                    Accessible.name: qsTr("Biểu tượng LinSub")
                }

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: Theme.space4

                    Text {
                        Layout.fillWidth: true
                        text: "LinSub"
                        color: Theme.text
                        font.family: Theme.fontFamily
                        font.pixelSize: TypeScale.title
                        font.weight: Font.DemiBold
                        textFormat: Text.PlainText
                    }

                    Text {
                        Layout.fillWidth: true
                        text: qsTr("Xử lý, dịch và lồng tiếng video trên Windows.")
                        color: Theme.textMuted
                        font.family: Theme.fontFamily
                        font.pixelSize: TypeScale.body
                        textFormat: Text.PlainText
                        wrapMode: Text.WordWrap
                    }

                    Text {
                        Layout.fillWidth: true
                        text: qsTr("Miễn phí sử dụng · Mã nguồn công khai")
                        color: Theme.textSubtle
                        font.family: Theme.fontFamily
                        font.pixelSize: TypeScale.label
                        textFormat: Text.PlainText
                    }
                }
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 1
                color: Theme.divider
            }

            Text {
                Layout.fillWidth: true
                text: qsTr("LinSub là ứng dụng chuyên nghiệp hỗ trợ tải video hàng loạt, nhận dạng giọng nói, dịch thuật AI, tạo phụ đề karaoke và lồng tiếng đa giọng đọc trên Windows.")
                color: Theme.text
                font.family: Theme.fontFamily
                font.pixelSize: TypeScale.control
                textFormat: Text.PlainText
                wrapMode: Text.WordWrap
                lineHeight: root.compactLayout ? 1.25 : 1.35
            }

            ColumnLayout {
                Layout.fillWidth: true
                spacing: Theme.space4

                AboutLinkRow {
                    label: qsTr("Phiên bản")
                    value: "LinSub " + (root.controller ? root.controller.currentAppVersion : "1.0.1")
                    copyValue: "LinSub " + (root.controller ? root.controller.currentAppVersion : "1.0.1")
                    linkEnabled: false
                }

                AboutLinkRow {
                    label: qsTr("Nền tảng")
                    value: "Windows 64-bit (Python 3.13 / PySide6)"
                    copyValue: "Windows 64-bit (Python 3.13 / PySide6)"
                    linkEnabled: false
                }

                AboutLinkRow {
                    label: qsTr("Hỗ trợ")
                    value: "support@linsub.com"
                    copyValue: "support@linsub.com"
                    linkEnabled: false
                }
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 70
                radius: Theme.radius
                color: Theme.surfaceStrong
                border.width: 1
                border.color: Theme.outlineStrong

                RowLayout {
                    anchors.fill: parent
                    anchors.margins: Theme.space12
                    spacing: Theme.space16

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 4

                        Text {
                            text: qsTr("LinSub Pro Video Suite")
                            color: Theme.text
                            font.family: Theme.fontFamily
                            font.pixelSize: TypeScale.section
                            font.weight: Font.DemiBold
                        }

                        Text {
                            text: qsTr("Dịch video · Tạo phụ đề · Lồng tiếng AI · Tự động hóa")
                            color: Theme.textMuted
                            font.family: Theme.fontFamily
                            font.pixelSize: TypeScale.label
                        }
                    }
                }
            }
        }
    }

    footerActions: StudioButton {
        text: qsTr("Đóng")
        variant: "primary"
        onClicked: root.close()
    }
}
