pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Layouts
import "."

ColumnLayout {
    id: root

    property bool editable: true
    signal styleCommitted()

    readonly property var projectStyle: AppController.manualEditorDocumentModel.defaultSubtitleStyle || ({})
    readonly property real currentPositionY: Number(
        root.projectStyle.position_y_percent !== undefined
            ? root.projectStyle.position_y_percent
            : (AppController.subtitlePositionYPercent || 90)
    )
    readonly property int currentFontSize: Math.round(Number(
        root.projectStyle.font_size !== undefined
            ? root.projectStyle.font_size
            : (AppController.subtitleFontSize || 60)
    ))

    spacing: Theme.space12

    function applyPatch(patch) {
        if (patch.position_y_percent !== undefined) {
            AppController.subtitlePositionYPercent = patch.position_y_percent;
        }
        if (patch.font_size !== undefined) {
            AppController.subtitleFontSize = patch.font_size;
        }
        if (AppController.applyTextStyle([], patch, "project")) {
            AppController.saveSelectedVideoSettings();
            root.styleCommitted();
        }
    }

    // 1. Cỡ chữ
    RowLayout {
        Layout.fillWidth: true
        spacing: Theme.space8

        SettingLabel {
            Layout.fillWidth: true
            text: qsTr("Cỡ chữ")
        }
        NumericField {
            objectName: "manualSubtitleFontSize"
            from: 16
            to: 180
            value: root.currentFontSize
            suffix: " px"
            enabled: root.editable
            onValueModified: root.applyPatch({ "font_size": value })
        }
    }

    // 2. Vị trí dọc (Y)
    ColumnLayout {
        Layout.fillWidth: true
        spacing: Theme.space6

        SettingLabel {
            Layout.fillWidth: true
            text: qsTr("Vị trí dọc (Y)")
        }

        ValueSlider {
            objectName: "manualSubtitlePositionSlider"
            Layout.fillWidth: true
            from: 10
            to: 98
            stepSize: 1
            suffix: "%"
            value: root.currentPositionY
            enabled: root.editable
            onPreviewed: function(val) {
                root.applyPatch({ "position_y_percent": Math.round(val) });
            }
            onCommitted: function(beforeVal, val) {
                root.applyPatch({ "position_y_percent": Math.round(val) });
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.space4

            StudioButton {
                Layout.fillWidth: true
                text: qsTr("Dưới (90%)")
                variant: Math.round(root.currentPositionY) >= 85 ? "primary" : "secondary"
                enabled: root.editable
                onClicked: root.applyPatch({ "position_y_percent": 90 })
            }
            StudioButton {
                Layout.fillWidth: true
                text: qsTr("Giữa (50%)")
                variant: Math.abs(Math.round(root.currentPositionY) - 50) <= 5 ? "primary" : "secondary"
                enabled: root.editable
                onClicked: root.applyPatch({ "position_y_percent": 50 })
            }
            StudioButton {
                Layout.fillWidth: true
                text: qsTr("Trên (15%)")
                variant: Math.round(root.currentPositionY) <= 25 ? "primary" : "secondary"
                enabled: root.editable
                onClicked: root.applyPatch({ "position_y_percent": 15 })
            }
        }
    }

    Rectangle {
        Layout.fillWidth: true
        Layout.preferredHeight: 1
        color: Theme.divider
    }

    // 3. Compact style bar (Đậm, Nghiêng, Màu chữ, Màu karaoke, Viền, Màu viền)
    CompactTextStyleBar {
        Layout.fillWidth: true
        style: root.projectStyle
        karaokeEnabled: true
        enabled: root.editable
        onChangeRequested: function(patch) {
            root.applyPatch(patch);
        }
    }

    // 4. Khung hướng dẫn trực quan
    InlineBanner {
        Layout.fillWidth: true
        tone: "info"
        message: qsTr("Mẹo: Bạn có thể nhấp trực tiếp vào chữ phụ đề trên video để kéo đổi vị trí hoặc kéo các góc để chỉnh cỡ chữ.")
    }
}
