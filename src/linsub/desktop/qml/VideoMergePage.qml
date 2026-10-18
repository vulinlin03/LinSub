pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtMultimedia
import QtQuick.Dialogs
import "."

Item {
    id: root

    readonly property var mergeCtrl: AppController.videoMerge
    property real timelineZoom: 1.0
    readonly property real pixelsPerSecond: 25 * timelineZoom
    readonly property real totalTrackWidth: Math.max(1, (root.mergeCtrl.totalDurationMs / 1000.0) * pixelsPerSecond)
    property int playbackPositionMs: 0
    property bool isPlaying: videoPlayer.playbackState === MediaPlayer.PlayingState

    DropArea {
        anchors.fill: parent
        onDropped: function(drop) {
            if (drop.hasUrls) {
                let paths = [];
                for (let i = 0; i < drop.urls.length; ++i) {
                    let u = drop.urls[i].toString();
                    if (u.startsWith("file:///")) {
                        u = u.substring(8);
                        if (Qt.platform.os === "windows" && u.length > 2 && u.charAt(1) === ":") {
                            // keep drive letter
                        } else if (Qt.platform.os === "windows" && u.startsWith("/")) {
                            u = u.substring(1);
                        }
                    }
                    paths.push(decodeURIComponent(u));
                }
                root.mergeCtrl.addVideos(paths);
            }
        }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: Theme.space16
        spacing: Theme.space12

        // =====================================================================
        // ZONE 1: TOP - PREVIEW MONITOR & SIDE PANEL SẮP XẾP THỨ TỰ
        // =====================================================================
        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.minimumHeight: 240
            spacing: Theme.space12

            // 1.1 MÀN HÌNH XEM TRƯỚC (PREVIEW MONITOR)
            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                color: Theme.video
                radius: Theme.radius
                border.width: 1
                border.color: Theme.outline

                // Empty state placeholder
                ColumnLayout {
                    anchors.centerIn: parent
                    visible: root.mergeCtrl.clipCount === 0
                    spacing: Theme.space12

                    Rectangle {
                        Layout.alignment: Qt.AlignHCenter
                        Layout.preferredWidth: 64
                        Layout.preferredHeight: 64
                        radius: 32
                        color: Theme.surfaceElevated
                        border.width: 1
                        border.color: Theme.outlineStrong

                        FluentIcon {
                            anchors.centerIn: parent
                            name: "video"
                            iconColor: Theme.interactive
                            iconSize: 28
                        }
                    }

                    Text {
                        Layout.alignment: Qt.AlignHCenter
                        text: qsTr("Kéo thả nhiều video vào đây hoặc bấm để chọn tệp")
                        color: Theme.text
                        font.family: Theme.fontFamily
                        font.pixelSize: TypeScale.title
                        font.weight: Font.DemiBold
                    }

                    Text {
                        Layout.alignment: Qt.AlignHCenter
                        text: qsTr("Ghép nối các đoạn video thành một video duy nhất với dòng thời gian chuẩn CapCut")
                        color: Theme.textMuted
                        font.family: Theme.fontFamily
                        font.pixelSize: TypeScale.control
                    }

                    StudioButton {
                        Layout.alignment: Qt.AlignHCenter
                        Layout.topMargin: Theme.space8
                        variant: "primary"
                        iconName: "add"
                        text: qsTr("Chọn video từ máy")
                        onClicked: root.mergeCtrl.chooseVideos()
                    }
                }

                // Video Player
                Item {
                    anchors.fill: parent
                    visible: root.mergeCtrl.clipCount > 0

                    MediaPlayer {
                        id: videoPlayer
                        source: root.mergeCtrl.currentPreviewSource
                        audioOutput: AudioOutput { id: audioOut; volume: 1.0 }
                        videoOutput: videoOutput
                        onPositionChanged: {
                            if (videoPlayer.playbackState === MediaPlayer.PlayingState) {
                                const curClip = root.mergeCtrl.clips[root.mergeCtrl.activeClipIndex];
                                if (curClip) {
                                    root.playbackPositionMs = (curClip.startMs || 0) + videoPlayer.position;
                                }
                            }
                        }
                        onMediaStatusChanged: {
                            if (videoPlayer.mediaStatus === MediaPlayer.EndOfMedia) {
                                if (root.mergeCtrl.activeClipIndex < root.mergeCtrl.clipCount - 1) {
                                    root.mergeCtrl.activeClipIndex += 1;
                                    videoPlayer.play();
                                } else {
                                    videoPlayer.pause();
                                    root.playbackPositionMs = 0;
                                }
                            }
                        }
                    }

                    VideoOutput {
                        id: videoOutput
                        anchors.fill: parent
                        anchors.bottomMargin: 46
                        fillMode: VideoOutput.PreserveAspectFit
                        endOfStreamPolicy: VideoOutput.KeepLastFrame
                    }

                    Image {
                        anchors.fill: videoOutput
                        source: {
                            const idx = root.mergeCtrl.activeClipIndex;
                            const list = root.mergeCtrl.clips;
                            if (idx >= 0 && idx < list.length) {
                                return list[idx].thumbnail || "";
                            }
                            return "";
                        }
                        fillMode: Image.PreserveAspectFit
                        asynchronous: true
                        visible: videoPlayer.playbackState !== MediaPlayer.PlayingState && status === Image.Ready
                    }

                    // Transport player bar
                    Rectangle {
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.bottom: parent.bottom
                        height: 44
                        color: Theme.captionOverlay

                        RowLayout {
                            anchors.fill: parent
                            anchors.leftMargin: Theme.space12
                            anchors.rightMargin: Theme.space12
                            spacing: Theme.space8

                            StudioIconButton {
                                iconName: root.isPlaying ? "pause" : "play"
                                toolTipText: root.isPlaying ? qsTr("Tạm dừng") : qsTr("Phát")
                                onClicked: {
                                    if (root.isPlaying)
                                        videoPlayer.pause();
                                    else
                                        videoPlayer.play();
                                }
                            }

                            // Progress seeker
                            Slider {
                                id: progressSlider
                                Layout.fillWidth: true
                                from: 0
                                to: Math.max(1, root.mergeCtrl.totalDurationMs)
                                value: root.playbackPositionMs
                                onMoved: {
                                    root.seekToSequencePosition(value);
                                }
                                background: Rectangle {
                                    x: progressSlider.leftPadding
                                    y: progressSlider.topPadding + progressSlider.availableHeight / 2 - height / 2
                                    implicitWidth: 200
                                    implicitHeight: 4
                                    width: progressSlider.availableWidth
                                    height: implicitHeight
                                    radius: 2
                                    color: Theme.surfaceMuted
                                    Rectangle {
                                        width: progressSlider.visualPosition * parent.width
                                        height: parent.height
                                        color: Theme.interactive
                                        radius: 2
                                    }
                                }
                                handle: Rectangle {
                                    x: progressSlider.leftPadding + progressSlider.visualPosition * (progressSlider.availableWidth - width)
                                    y: progressSlider.topPadding + progressSlider.availableHeight / 2 - height / 2
                                    implicitWidth: 12
                                    implicitHeight: 12
                                    radius: 6
                                    color: Theme.text
                                    border.width: 2
                                    border.color: Theme.interactive
                                }
                            }

                            // Time display
                            Text {
                                text: root.formatMs(root.playbackPositionMs) + " / " + root.mergeCtrl.totalDurationFormatted
                                color: Theme.text
                                font.family: Theme.fontFamily
                                font.pixelSize: TypeScale.metadata
                            }

                            StudioIconButton {
                                iconName: audioOut.muted ? "muted" : "volume"
                                toolTipText: audioOut.muted ? qsTr("Bật âm") : qsTr("Tắt tiếng")
                                onClicked: audioOut.muted = !audioOut.muted
                            }
                        }
                    }
                }
            }

            // 1.2 CỘT BÊN HÔNG: BẢNG SẮP XẾP THỨ TỰ CLIP
            Rectangle {
                Layout.preferredWidth: 340
                Layout.minimumWidth: 280
                Layout.fillHeight: true
                color: Theme.surfaceElevated
                radius: Theme.radius
                border.width: 1
                border.color: Theme.outlineStrong
                clip: true

                ColumnLayout {
                    anchors.fill: parent
                    anchors.leftMargin: 16
                    anchors.rightMargin: 16
                    anchors.topMargin: 14
                    anchors.bottomMargin: 14
                    spacing: Theme.space8

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Theme.space8

                        Text {
                            text: qsTr("Thứ tự video")
                            color: Theme.text
                            font.family: Theme.fontFamily
                            font.pixelSize: TypeScale.body
                            font.weight: Font.Bold
                            Layout.alignment: Qt.AlignVCenter
                        }

                        Rectangle {
                            height: 18
                            width: Math.max(18, countBadge.implicitWidth + 10)
                            radius: 9
                            color: Theme.interactive
                            Layout.alignment: Qt.AlignVCenter

                            Text {
                                id: countBadge
                                anchors.centerIn: parent
                                text: String(root.mergeCtrl.clipCount)
                                color: Theme.textOnAccent
                                font.family: Theme.fontFamily
                                font.pixelSize: 10
                                font.bold: true
                            }
                        }

                        Item { Layout.fillWidth: true }

                        StudioIconButton {
                            controlSize: 26
                            iconName: root.mergeCtrl.sortAscending ? "sort" : "sortDesc"
                            enabled: root.mergeCtrl.clipCount >= 2
                            onClicked: root.mergeCtrl.toggleSortByName()
                        }

                        StudioIconButton {
                            controlSize: 26
                            iconName: "refresh"
                            enabled: root.mergeCtrl.clipCount >= 2
                            onClicked: root.mergeCtrl.reverseClips()
                        }
                    }

                    Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: Theme.divider }

                    Text {
                        Layout.alignment: Qt.AlignCenter
                        visible: root.mergeCtrl.clipCount === 0
                        text: qsTr("Chưa có video nào.\nHãy thêm video để sắp xếp thứ tự.")
                        color: Theme.textMuted
                        font.family: Theme.fontFamily
                        font.pixelSize: TypeScale.metadata
                        horizontalAlignment: Text.AlignHCenter
                    }

                    ListView {
                        id: sideOrderList
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        spacing: 6
                        model: root.mergeCtrl.clips
                        boundsBehavior: Flickable.StopAtBounds

                        property int dragSourceIndex: -1
                        property int dropHoverIndex: -1

                        delegate: Rectangle {
                            id: orderCard
                            required property int index
                            required property var modelData

                            readonly property bool isBeingDragged: sideOrderList.dragSourceIndex === orderCard.index
                            readonly property bool isDropTarget: sideOrderList.dropHoverIndex === orderCard.index && sideOrderList.dragSourceIndex >= 0 && sideOrderList.dragSourceIndex !== orderCard.index

                            width: sideOrderList.width
                            height: 52
                            radius: Theme.radiusSmall
                            color: isBeingDragged ? "#3a2d20" : root.mergeCtrl.activeClipIndex === orderCard.index ? "#2e2922" : Theme.surface
                            border.width: isBeingDragged ? 2 : (root.mergeCtrl.activeClipIndex === orderCard.index ? 2 : 1)
                            border.color: isBeingDragged ? Theme.interactiveHover : (root.mergeCtrl.activeClipIndex === orderCard.index ? Theme.interactive : Theme.divider)
                            opacity: isBeingDragged ? 0.85 : 1.0
                            z: isBeingDragged ? 50 : 1

                            // Visual drop indicator line
                            Rectangle {
                                anchors.left: parent.left
                                anchors.right: parent.right
                                anchors.top: parent.top
                                height: 3
                                radius: 1.5
                                color: Theme.interactive
                                visible: orderCard.isDropTarget && sideOrderList.dragSourceIndex > orderCard.index
                                z: 100
                            }
                            Rectangle {
                                anchors.left: parent.left
                                anchors.right: parent.right
                                anchors.bottom: parent.bottom
                                height: 3
                                radius: 1.5
                                color: Theme.interactive
                                visible: orderCard.isDropTarget && sideOrderList.dragSourceIndex < orderCard.index
                                z: 100
                            }

                            MouseArea {
                                id: cardDragArea
                                anchors.fill: parent
                                hoverEnabled: true
                                cursorShape: dragging ? Qt.ClosedHandCursor : Qt.PointingHandCursor
                                property real pressY: 0
                                property bool dragging: false

                                onPressed: function(mouse) {
                                    pressY = mouse.y;
                                    dragging = false;
                                }

                                onPositionChanged: function(mouse) {
                                    if (pressed) {
                                        if (!dragging && Math.abs(mouse.y - pressY) > 5) {
                                            dragging = true;
                                            sideOrderList.dragSourceIndex = orderCard.index;
                                        }
                                        if (dragging) {
                                            const mapPos = mapToItem(sideOrderList.contentItem, mouse.x, mouse.y);
                                            const rowH = 58;
                                            const target = Math.max(0, Math.min(root.mergeCtrl.clipCount - 1, Math.floor(mapPos.y / rowH)));
                                            sideOrderList.dropHoverIndex = target;
                                        }
                                    }
                                }

                                onReleased: function(mouse) {
                                    if (dragging) {
                                        const from = sideOrderList.dragSourceIndex;
                                        const to = sideOrderList.dropHoverIndex;
                                        if (from >= 0 && to >= 0 && from !== to) {
                                            root.mergeCtrl.moveClip(from, to);
                                            root.mergeCtrl.activeClipIndex = to;
                                        }
                                    } else {
                                        root.mergeCtrl.activeClipIndex = orderCard.index;
                                        root.playbackPositionMs = orderCard.modelData.startMs || 0;
                                        videoPlayer.position = 0;
                                    }
                                    dragging = false;
                                    sideOrderList.dragSourceIndex = -1;
                                    sideOrderList.dropHoverIndex = -1;
                                }

                                onCanceled: {
                                    dragging = false;
                                    sideOrderList.dragSourceIndex = -1;
                                    sideOrderList.dropHoverIndex = -1;
                                }
                            }

                            RowLayout {
                                anchors.fill: parent
                                anchors.margins: 4
                                spacing: 5

                                Text {
                                    text: "⋮⋮"
                                    color: Theme.textMuted
                                    font.pixelSize: 13
                                    font.bold: true
                                    Layout.alignment: Qt.AlignVCenter
                                    Layout.leftMargin: 2
                                }

                                Rectangle {
                                    Layout.preferredWidth: 20
                                    Layout.preferredHeight: 20
                                    radius: 10
                                    color: root.mergeCtrl.activeClipIndex === orderCard.index ? Theme.interactive : Theme.surfaceMuted

                                    Text {
                                        anchors.centerIn: parent
                                        text: String(orderCard.index + 1)
                                        color: root.mergeCtrl.activeClipIndex === orderCard.index ? Theme.textOnAccent : Theme.text
                                        font.family: Theme.fontFamily
                                        font.pixelSize: 11
                                        font.bold: true
                                    }
                                }

                                Rectangle {
                                    Layout.preferredWidth: 54
                                    Layout.preferredHeight: 34
                                    radius: 3
                                    clip: true
                                    color: "#000000"
                                    border.width: 1
                                    border.color: Theme.outlineStrong

                                    Image {
                                        anchors.fill: parent
                                        source: orderCard.modelData.thumbnail || ""
                                        fillMode: Image.PreserveAspectFit
                                        asynchronous: true
                                    }
                                }

                                ColumnLayout {
                                    Layout.fillWidth: true
                                    spacing: 2

                                    Text {
                                        Layout.fillWidth: true
                                        text: String(orderCard.modelData.name || "")
                                        color: Theme.text
                                        font.family: Theme.fontFamily
                                        font.pixelSize: 11
                                        font.weight: Font.DemiBold
                                        elide: Text.ElideRight
                                    }

                                    Text {
                                        text: String(orderCard.modelData.durationFormatted || "")
                                        color: Theme.textMuted
                                        font.family: Theme.fontFamily
                                        font.pixelSize: 10
                                    }
                                }

                                RowLayout {
                                    spacing: 1

                                    StudioIconButton {
                                        controlSize: 22
                                        iconName: "chevronUp"
                                        enabled: orderCard.index > 0
                                        toolTipText: qsTr("Đưa lên trước")
                                        onClicked: root.mergeCtrl.moveClip(orderCard.index, orderCard.index - 1)
                                    }

                                    StudioIconButton {
                                        controlSize: 22
                                        iconName: "chevronDown"
                                        enabled: orderCard.index < root.mergeCtrl.clipCount - 1
                                        toolTipText: qsTr("Đưa về sau")
                                        onClicked: root.mergeCtrl.moveClip(orderCard.index, orderCard.index + 1)
                                    }

                                    StudioIconButton {
                                        controlSize: 22
                                        iconName: "close"
                                        toolTipText: qsTr("Xóa clip")
                                        onClicked: root.mergeCtrl.removeClip(orderCard.index)
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }

        // =====================================================================
        // ZONE 2: MIDDLE - HORIZONTAL CONTROL TOOLBAR (DƯỚI MÀN HÌNH XEM TRƯỚC)
        // =====================================================================
        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 48
            color: Theme.surfaceElevated
            radius: Theme.radiusSmall
            border.width: 1
            border.color: Theme.outlineStrong

            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: Theme.space12
                anchors.rightMargin: Theme.space12
                spacing: Theme.space8

                StudioButton {
                    iconName: "add"
                    text: qsTr("Thêm video")
                    variant: "secondary"
                    compact: true
                    onClicked: root.mergeCtrl.chooseVideos()
                }

                Rectangle {
                    Layout.preferredWidth: 1
                    Layout.preferredHeight: 20
                    color: Theme.divider
                }

                StudioComboBox {
                    Layout.preferredWidth: 140
                    model: [
                        { value: "auto", label: qsTr("Tự động (gốc)") },
                        { value: "16:9", label: "16:9 (Ngang)" },
                        { value: "9:16", label: "9:16 (Dọc)" },
                        { value: "1:1", label: "1:1 (Vuông)" }
                    ]
                    textRole: "label"
                    valueRole: "value"
                    currentIndex: 0
                    onActivated: root.mergeCtrl.setAspectRatio(currentValue)
                }

                StudioComboBox {
                    Layout.preferredWidth: 160
                    model: [
                        { value: "fast", label: qsTr("Siêu tốc (Lossless)") },
                        { value: "standard", label: qsTr("Chuẩn hóa tỷ lệ") }
                    ]
                    textRole: "label"
                    valueRole: "value"
                    currentIndex: 0
                    onActivated: root.mergeCtrl.setMergeMode(currentValue)
                }

                Rectangle {
                    Layout.preferredWidth: 1
                    Layout.preferredHeight: 20
                    color: Theme.divider
                }

                Text {
                    text: root.mergeCtrl.totalDurationFormatted + " (" + root.mergeCtrl.clipCount + " clip)"
                    color: Theme.text
                    font.family: Theme.fontFamily
                    font.pixelSize: TypeScale.control
                    font.weight: Font.DemiBold
                }

                Item { Layout.fillWidth: true; Layout.minimumWidth: 8 } // Spacer

                StudioButton {
                    variant: "secondary"
                    iconName: "download"
                    text: qsTr("Xuất video")
                    compact: true
                    enabled: root.mergeCtrl.clipCount >= 2 && !root.mergeCtrl.isBusy
                    onClicked: root.mergeCtrl.startMerge("")
                }

                StudioButton {
                    variant: "primary"
                    iconName: "play"
                    text: qsTr("Ghép & Lồng tiếng ngay")
                    compact: true
                    enabled: root.mergeCtrl.clipCount >= 2 && !root.mergeCtrl.isBusy
                    onClicked: root.mergeCtrl.mergeAndCreateProject("single")
                }
            }
        }

        // =====================================================================
        // ZONE 3: BOTTOM - MULTI-CLIP HORIZONTAL TIMELINE (CHUẨN CAPCUT)
        // =====================================================================
        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 200
            color: Theme.surface
            radius: Theme.radiusSmall
            border.width: 1
            border.color: Theme.outlineStrong

            ColumnLayout {
                anchors.fill: parent
                spacing: 0

                // Timeline Top Header Bar
                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 32
                    color: Theme.surfaceElevated

                    RowLayout {
                        anchors.fill: parent
                        anchors.leftMargin: Theme.space12
                        anchors.rightMargin: Theme.space12
                        spacing: Theme.space8

                        Text {
                            text: qsTr("DÒNG THỜI GIAN CAPCUT")
                            color: Theme.text
                            font.family: Theme.fontFamily
                            font.pixelSize: TypeScale.metadata
                            font.weight: Font.Bold
                        }

                        Rectangle {
                            Layout.preferredWidth: 1
                            Layout.preferredHeight: 16
                            color: Theme.divider
                        }

                        Text {
                            text: root.formatMs(root.playbackPositionMs) + " / " + root.mergeCtrl.totalDurationFormatted
                            color: Theme.interactive
                            font.family: Theme.fontFamily
                            font.pixelSize: TypeScale.metadata
                            font.weight: Font.DemiBold
                        }

                        Item { Layout.fillWidth: true }

                        StudioIconButton {
                            iconName: "remove"
                            toolTipText: qsTr("Thu nhỏ timeline")
                            onClicked: root.timelineZoom = Math.max(0.25, root.timelineZoom - 0.25)
                        }

                        Text {
                            text: Math.round(root.timelineZoom * 100) + "%"
                            color: Theme.textMuted
                            font.family: Theme.fontFamily
                            font.pixelSize: TypeScale.metadata
                        }

                        StudioIconButton {
                            iconName: "add"
                            toolTipText: qsTr("Phóng to timeline")
                            onClicked: root.timelineZoom = Math.min(3.0, root.timelineZoom + 0.25)
                        }

                        StudioIconButton {
                            iconName: "delete"
                            toolTipText: qsTr("Xóa tất cả clip")
                            enabled: root.mergeCtrl.clipCount > 0 && !root.mergeCtrl.isBusy
                            onClicked: root.mergeCtrl.clearClips()
                        }
                    }
                }

                Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: Theme.divider }

                // Timeline Main Body (Track Header on Left + Scrollable Ruler & Tracks on Right)
                RowLayout {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    spacing: 0

                    // Left Fixed Track Header (like CapCut)
                    Rectangle {
                        Layout.preferredWidth: 110
                        Layout.fillHeight: true
                        color: "#181716"
                        border.width: 1
                        border.color: Theme.divider

                        ColumnLayout {
                            anchors.fill: parent
                            spacing: 0

                            // Top cell matching Ruler height
                            Rectangle {
                                Layout.fillWidth: true
                                Layout.preferredHeight: 28
                                color: "#1f1d1b"
                                border.width: 1
                                border.color: Theme.divider

                                Text {
                                    anchors.centerIn: parent
                                    text: root.formatMs(root.playbackPositionMs)
                                    color: Theme.interactive
                                    font.family: Theme.fontFamily
                                    font.pixelSize: 11
                                    font.bold: true
                                }
                            }

                            // Body cell matching Track
                            Rectangle {
                                Layout.fillWidth: true
                                Layout.fillHeight: true
                                color: "transparent"

                                ColumnLayout {
                                    anchors.centerIn: parent
                                    spacing: 4

                                    Text {
                                        Layout.alignment: Qt.AlignHCenter
                                        text: qsTr("Video 1")
                                        color: Theme.text
                                        font.family: Theme.fontFamily
                                        font.pixelSize: 12
                                        font.bold: true
                                    }

                                    Text {
                                        Layout.alignment: Qt.AlignHCenter
                                        text: root.mergeCtrl.clipCount + qsTr(" đoạn clip")
                                        color: Theme.textMuted
                                        font.family: Theme.fontFamily
                                        font.pixelSize: 10
                                    }
                                }
                            }
                        }
                    }

                    // Right Scrollable Area: Ruler + Tracks + Playhead
                    Flickable {
                        id: timelineFlick
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        contentWidth: Math.max(width, root.totalTrackWidth + 120)
                        contentHeight: height
                        boundsBehavior: Flickable.StopAtBounds

                        Item {
                            width: Math.max(timelineFlick.width, root.totalTrackWidth + 120)
                            height: timelineFlick.height

                            // 1. TIME RULER (Thước đo thời gian)
                            Canvas {
                                id: rulerCanvas
                                x: 0
                                y: 0
                                width: parent.width
                                height: 28

                                onPaint: {
                                    const ctx = getContext("2d");
                                    ctx.clearRect(0, 0, width, height);
                                    ctx.fillStyle = "#1e1d1b";
                                    ctx.fillRect(0, 0, width, height);

                                    const pps = root.pixelsPerSecond;
                                    if (pps <= 0) return;

                                    let step = 10;
                                    if (pps >= 40) step = 2;
                                    else if (pps >= 20) step = 5;
                                    else if (pps >= 10) step = 10;
                                    else if (pps >= 4) step = 30;
                                    else step = 60;

                                    const totalSec = Math.ceil(root.mergeCtrl.totalDurationMs / 1000.0) + 30;
                                    ctx.strokeStyle = "#44403a";
                                    ctx.fillStyle = "#a8a29e";
                                    ctx.font = "10px sans-serif";
                                    ctx.lineWidth = 1;

                                    for (let sec = 0; sec <= totalSec; sec += step) {
                                        const px = Math.round(sec * pps);
                                        if (px > width) break;

                                        ctx.beginPath();
                                        ctx.moveTo(px, 14);
                                        ctx.lineTo(px, height);
                                        ctx.stroke();

                                        const m = Math.floor(sec / 60);
                                        const s = sec % 60;
                                        const timeStr = (m < 10 ? "0" + m : m) + ":" + (s < 10 ? "0" + s : s);
                                        ctx.fillText(timeStr, px + 3, 11);

                                        const subStep = step / 5;
                                        if (subStep >= 1 && pps * subStep >= 5) {
                                            for (let sub = 1; sub < 5; ++sub) {
                                                const subPx = Math.round((sec + sub * subStep) * pps);
                                                if (subPx > width) break;
                                                ctx.beginPath();
                                                ctx.moveTo(subPx, 20);
                                                ctx.lineTo(subPx, height);
                                                ctx.stroke();
                                            }
                                        }
                                    }
                                }

                                Connections {
                                    target: root
                                    function onTimelineZoomChanged() { rulerCanvas.requestPaint(); }
                                }
                                Connections {
                                    target: root.mergeCtrl
                                    function onClipsChanged() { rulerCanvas.requestPaint(); }
                                }

                                MouseArea {
                                    anchors.fill: parent
                                    cursorShape: Qt.PointingHandCursor
                                    onPressed: function(mouse) { root.seekFromTimelineX(mouse.x); }
                                    onPositionChanged: function(mouse) { if (pressed) root.seekFromTimelineX(mouse.x); }
                                }
                            }

                            // 2. VIDEO TRACK LANE
                            Rectangle {
                                x: 0
                                y: 28
                                width: parent.width
                                height: parent.height - 28
                                color: "#121110"

                                // Background track line
                                Rectangle {
                                    anchors.left: parent.left
                                    anchors.right: parent.right
                                    anchors.top: parent.top
                                    height: 1
                                    color: Theme.divider
                                }

                                MouseArea {
                                    anchors.fill: parent
                                    onPressed: function(mouse) { root.seekFromTimelineX(mouse.x); }
                                    onPositionChanged: function(mouse) { if (pressed) root.seekFromTimelineX(mouse.x); }
                                }

                                // Clips on Track
                                Repeater {
                                    model: root.mergeCtrl.clips

                                    delegate: Item {
                                        id: clipBlock
                                        required property int index
                                        required property var modelData

                                        x: Math.round(((modelData.startMs || 0) / 1000.0) * root.pixelsPerSecond)
                                        y: 8
                                        width: Math.max(90, Math.round((modelData.duration || 1) * root.pixelsPerSecond))
                                        height: parent.height - 16

                                        Rectangle {
                                            anchors.fill: parent
                                            radius: 4
                                            clip: true
                                            color: root.mergeCtrl.activeClipIndex === clipBlock.index ? "#2d261e" : "#1f1d1a"
                                            border.width: root.mergeCtrl.activeClipIndex === clipBlock.index ? 2 : 1
                                            border.color: root.mergeCtrl.activeClipIndex === clipBlock.index ? Theme.interactive : "#423d36"

                                            MouseArea {
                                                anchors.fill: parent
                                                onClicked: {
                                                    root.mergeCtrl.activeClipIndex = clipBlock.index;
                                                    root.playbackPositionMs = clipBlock.modelData.startMs || 0;
                                                    videoPlayer.position = 0;
                                                }
                                            }

                                            RowLayout {
                                                anchors.fill: parent
                                                anchors.margins: 4
                                                spacing: 6

                                                // Crisp 16:9 Thumbnail badge (never blurry)
                                                Rectangle {
                                                    Layout.preferredWidth: Math.min(88, Math.max(40, parent.width * 0.35))
                                                    Layout.fillHeight: true
                                                    radius: 3
                                                    clip: true
                                                    color: "#0c0b0a"
                                                    border.width: 1
                                                    border.color: "#4a453d"

                                                    Image {
                                                        anchors.fill: parent
                                                        source: clipBlock.modelData.thumbnail || ""
                                                        fillMode: Image.PreserveAspectCrop
                                                        asynchronous: true
                                                    }
                                                }

                                                // Info column
                                                ColumnLayout {
                                                    Layout.fillWidth: true
                                                    Layout.fillHeight: true
                                                    spacing: 2

                                                    RowLayout {
                                                        Layout.fillWidth: true
                                                        spacing: 4

                                                        Rectangle {
                                                            Layout.preferredWidth: 16
                                                            Layout.preferredHeight: 16
                                                            radius: 8
                                                            color: Theme.interactive

                                                            Text {
                                                                anchors.centerIn: parent
                                                                text: String(clipBlock.index + 1)
                                                                color: Theme.textOnAccent
                                                                font.family: Theme.fontFamily
                                                                font.pixelSize: 10
                                                                font.bold: true
                                                            }
                                                        }

                                                        Text {
                                                            Layout.fillWidth: true
                                                            text: String(clipBlock.modelData.name || "")
                                                            color: Theme.text
                                                            font.family: Theme.fontFamily
                                                            font.pixelSize: 11
                                                            font.bold: true
                                                            elide: Text.ElideRight
                                                        }
                                                    }

                                                    RowLayout {
                                                        Layout.fillWidth: true
                                                        spacing: 4

                                                        Text {
                                                            text: String(clipBlock.modelData.durationFormatted || "")
                                                            color: Theme.textMuted
                                                            font.family: Theme.fontFamily
                                                            font.pixelSize: 10
                                                        }

                                                        Item { Layout.fillWidth: true }

                                                        StudioIconButton {
                                                            Layout.preferredWidth: 18
                                                            Layout.preferredHeight: 18
                                                            iconName: "chevronUp"
                                                            rotation: -90
                                                            enabled: clipBlock.index > 0
                                                            toolTipText: qsTr("Chuyển lên trước")
                                                            onClicked: root.mergeCtrl.moveClip(clipBlock.index, clipBlock.index - 1)
                                                        }

                                                        StudioIconButton {
                                                            Layout.preferredWidth: 18
                                                            Layout.preferredHeight: 18
                                                            iconName: "chevronDown"
                                                            rotation: -90
                                                            enabled: clipBlock.index < root.mergeCtrl.clipCount - 1
                                                            toolTipText: qsTr("Chuyển về sau")
                                                            onClicked: root.mergeCtrl.moveClip(clipBlock.index, clipBlock.index + 1)
                                                        }

                                                        StudioIconButton {
                                                            Layout.preferredWidth: 18
                                                            Layout.preferredHeight: 18
                                                            iconName: "close"
                                                            toolTipText: qsTr("Xóa clip này")
                                                            onClicked: root.mergeCtrl.removeClip(clipBlock.index)
                                                        }
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }
                            }

                            // 3. CAPCUT PLAYHEAD NEEDLE (Kim đọc thời gian dọc toàn timeline)
                            Item {
                                id: playheadNeedle
                                x: Math.round((root.playbackPositionMs / 1000.0) * root.pixelsPerSecond) - 7
                                y: 0
                                width: 14
                                height: parent.height
                                z: 20

                                // Needle pointer head on ruler
                                Rectangle {
                                    x: 0
                                    y: 0
                                    width: 14
                                    height: 14
                                    color: Theme.interactive
                                    radius: 2
                                }

                                // Needle vertical line extending down through track
                                Rectangle {
                                    x: 6
                                    y: 14
                                    width: 2
                                    height: parent.height - 14
                                    color: Theme.interactive
                                }

                                MouseArea {
                                    anchors.fill: parent
                                    cursorShape: Qt.SizeHorCursor
                                    drag.target: parent
                                    drag.axis: Drag.XAxis
                                    drag.minimumX: -7
                                    drag.maximumX: Math.round((root.mergeCtrl.totalDurationMs / 1000.0) * root.pixelsPerSecond) - 7
                                    onPositionChanged: {
                                        if (drag.active) {
                                            root.seekFromTimelineX(parent.x + 7);
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    // =========================================================================
    // PROGRESS / BUSY OVERLAY
    // =========================================================================
    Rectangle {
        anchors.fill: parent
        visible: root.mergeCtrl.isBusy
        color: "#c011100f"
        z: 99

        MouseArea { anchors.fill: parent } // Block clicks underneath

        ColumnLayout {
            anchors.centerIn: parent
            spacing: Theme.space16

            BusyIndicator {
                Layout.alignment: Qt.AlignHCenter
                running: visible
                visible: root.mergeCtrl.isBusy
            }

            Text {
                Layout.alignment: Qt.AlignHCenter
                text: root.mergeCtrl.progressMessage
                color: Theme.text
                font.family: Theme.fontFamily
                font.pixelSize: TypeScale.title
                font.bold: true
            }

            Rectangle {
                Layout.preferredWidth: 320
                Layout.preferredHeight: 8
                radius: 4
                color: Theme.surfaceMuted

                Rectangle {
                    width: parent.width * Math.max(0.05, root.mergeCtrl.progress)
                    height: parent.height
                    radius: 4
                    color: Theme.interactive
                }
            }

            StudioButton {
                Layout.alignment: Qt.AlignHCenter
                variant: "secondary"
                text: qsTr("Hủy")
                onClicked: root.mergeCtrl.cancelMerge()
            }
        }
    }

    function seekFromTimelineX(x) {
        if (root.pixelsPerSecond <= 0) return;
        const sec = Math.max(0, x / root.pixelsPerSecond);
        const ms = Math.min(root.mergeCtrl.totalDurationMs, Math.round(sec * 1000));
        root.seekToSequencePosition(ms);
    }

    function formatMs(milliseconds) {
        const totalSeconds = Math.max(0, Math.floor(milliseconds / 1000));
        const s = totalSeconds % 60;
        const m = Math.floor(totalSeconds / 60) % 60;
        const h = Math.floor(totalSeconds / 3600);
        if (h > 0)
            return (h < 10 ? "0" + h : h) + ":" + (m < 10 ? "0" + m : m) + ":" + (s < 10 ? "0" + s : s);
        return (m < 10 ? "0" + m : m) + ":" + (s < 10 ? "0" + s : s);
    }

    function seekToSequencePosition(ms) {
        root.playbackPositionMs = ms;
        const res = root.mergeCtrl.resolvePosition(ms);
        if (res && res.clipIndex >= 0) {
            if (root.mergeCtrl.activeClipIndex !== res.clipIndex) {
                root.mergeCtrl.activeClipIndex = res.clipIndex;
            }
            videoPlayer.position = res.localMs;
        }
    }
}
