pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "."

AppDialog {
    id: root

    property string targetVideoId: ""
    property string targetProvider: "vieneu"
    property var speakerProfiles: []
    property var draftMapping: ({})
    property var voiceModel: []
    property bool isDetecting: false
    property int detectingProgress: 0
    property string detectingMessage: ""
    property string activePreviewVoice: ""

    signal mappingApplied(var mapping)

    title: qsTr("Phân vai giọng đọc nhân vật")
    subtitle: qsTr("Tùy chọn giọng đọc cho từng nhân vật được AI nhận diện từ video")
    preferredWidth: 680
    preferredHeight: 640
    maximumHeight: 740

    function isExcludedVoice(voice) {
        const v = String(voice || "");
        return v === "omnivoice:clone" || v === "vieneu:clone" || v === "omnivoice:multiple";
    }

    function refreshVoiceModel() {
        const rawOptions = AppController.manualVoiceOptionsForProvider(targetProvider) || [];
        const filtered = [];
        for (let i = 0; i < rawOptions.length; ++i) {
            const opt = rawOptions[i];
            if (!isExcludedVoice(opt.voice)) {
                filtered.push({
                    "voice": String(opt.voice || ""),
                    "label": String(opt.label || opt.voice || ""),
                    "gender": String(opt.gender || ""),
                    "accent": String(opt.accent || "")
                });
            }
        }
        voiceModel = filtered;
    }

    function refreshSpeakerProfiles() {
        const profiles = AppController.getSpeakerProfiles(targetVideoId, targetProvider) || [];
        speakerProfiles = profiles;
        const newMapping = Object.assign({}, draftMapping);
        for (let i = 0; i < profiles.length; ++i) {
            const item = profiles[i];
            const sid = String(item.speakerId || "");
            if (!newMapping[sid]) {
                newMapping[sid] = String(item.assignedVoice || item.defaultVoice || "");
            }
        }
        draftMapping = newMapping;
    }

    function openForSelectedVideo() {
        openForVideo(AppController.selectedVideoId, AppController.ttsProvider);
    }

    function openForVideo(videoId, provider) {
        targetVideoId = String(videoId || AppController.selectedVideoId || "");
        targetProvider = String(provider || AppController.ttsProvider || "vieneu");
        isDetecting = false;
        detectingProgress = 0;
        detectingMessage = "";
        activePreviewVoice = "";
        refreshVoiceModel();
        refreshSpeakerProfiles();
        open();
    }

    function saveAndApply() {
        AppController.saveSpeakerVoiceMapping(targetVideoId, draftMapping);
        mappingApplied(draftMapping);
        close();
    }

    function previewVoice(voice) {
        const v = String(voice || "");
        if (!v) return;
        activePreviewVoice = v;
        AppController.previewVoiceSample(targetProvider, v, AppController.manualVoiceLanguage);
    }

    onClosed: {
        activePreviewVoice = "";
    }

    Connections {
        target: AppController

        function onSpeakerProfilesChanged() {
            if (root.opened) {
                root.refreshSpeakerProfiles();
            }
        }

        function onSpeakerDetectionStateChanged(state, progress, message) {
            if (!root.opened) return;
            if (state === "detecting") {
                root.isDetecting = true;
                root.detectingProgress = progress;
                root.detectingMessage = String(message || "");
            } else if (state === "done") {
                root.isDetecting = false;
                root.detectingProgress = 100;
                root.refreshSpeakerProfiles();
            } else if (state === "error") {
                root.isDetecting = false;
                root.detectingMessage = String(message || qsTr("Lỗi khi nhận diện người nói"));
            }
        }
    }

    ColumnLayout {
        Layout.fillWidth: true
        Layout.fillHeight: true
        spacing: Theme.space12

        // Top information & Rescan action bar
        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.space12

            Rectangle {
                Layout.preferredHeight: 32
                implicitWidth: providerLabel.implicitWidth + Theme.space16
                radius: Theme.radiusSmall
                color: Theme.surfaceMuted
                border.width: 1
                border.color: Theme.divider

                Text {
                    id: providerLabel
                    anchors.centerIn: parent
                    text: root.targetProvider.indexOf("vieneu") === 0 ? qsTr("Công cụ: VieNeu (Giọng Việt)") : qsTr("Công cụ: OmniVoice")
                    color: Theme.textMuted
                    font.family: Theme.fontFamily
                    font.pixelSize: TypeScale.metadata
                }
            }

            Text {
                Layout.fillWidth: true
                text: root.speakerProfiles.length > 0
                      ? qsTr("Đã nhận diện %1 nhân vật").arg(root.speakerProfiles.length)
                      : (root.isDetecting ? qsTr("Đang phân tích...") : qsTr("Chưa quét nhân vật"))
                color: Theme.textSubtle
                font.family: Theme.fontFamily
                font.pixelSize: TypeScale.metadata
                elide: Text.ElideRight
            }

            StudioButton {
                text: root.isDetecting ? qsTr("Đang quét...") : qsTr("Nhận diện lại")
                iconName: "refresh"
                variant: "subtle"
                enabled: !root.isDetecting && root.targetVideoId.length > 0
                onClicked: AppController.detectSpeakers(root.targetVideoId)
            }
        }

        // Detecting status banner
        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 46
            visible: root.isDetecting
            radius: Theme.radius
            color: Theme.interactiveMuted
            border.width: 1
            border.color: Theme.interactiveOutline

            RowLayout {
                anchors.fill: parent
                anchors.margins: Theme.space12
                spacing: Theme.space12

                AppIcon {
                    glyph: "\uE72C"
                    iconColor: Theme.interactive
                }

                Text {
                    Layout.fillWidth: true
                    text: root.detectingMessage.length > 0 ? root.detectingMessage : qsTr("Đang nhận diện các nhân vật...")
                    color: Theme.text
                    font.family: Theme.fontFamily
                    font.pixelSize: TypeScale.control
                    elide: Text.ElideRight
                }

                Text {
                    text: root.detectingProgress + "%"
                    color: Theme.interactive
                    font.family: Theme.fontFamily
                    font.pixelSize: TypeScale.control
                    font.weight: Font.DemiBold
                }
            }
        }

        // Empty state: no speakers detected yet
        Rectangle {
            Layout.fillWidth: true
            Layout.fillHeight: true
            visible: !root.isDetecting && root.speakerProfiles.length === 0
            radius: Theme.radius
            color: Theme.surfaceElevated
            border.width: 1
            border.color: Theme.divider

            ColumnLayout {
                anchors.centerIn: parent
                spacing: Theme.space16
                width: Math.min(parent.width - 64, 480)

                Rectangle {
                    Layout.alignment: Qt.AlignHCenter
                    width: 56
                    height: 56
                    radius: 28
                    color: Theme.surfaceMuted
                    border.width: 1
                    border.color: Theme.outline

                    Text {
                        anchors.centerIn: parent
                        text: "🎭"
                        font.pixelSize: 28
                    }
                }

                Text {
                    Layout.fillWidth: true
                    text: qsTr("Chưa có danh sách nhân vật cho video này")
                    color: Theme.text
                    font.family: Theme.fontFamily
                    font.pixelSize: TypeScale.section
                    font.weight: Font.DemiBold
                    horizontalAlignment: Text.AlignHCenter
                }

                Text {
                    Layout.fillWidth: true
                    text: qsTr("Hệ thống sẽ phân tích phụ đề và giọng nói gốc để tách riêng từng nhân vật, ước tính độ cao giọng (F0) và phong cách nói.")
                    color: Theme.textMuted
                    font.family: Theme.fontFamily
                    font.pixelSize: TypeScale.control
                    horizontalAlignment: Text.AlignHCenter
                    wrapMode: Text.Wrap
                }

                StudioButton {
                    Layout.alignment: Qt.AlignHCenter
                    text: qsTr("Quét nhận diện nhân vật ngay")
                    iconName: "search"
                    variant: "primary"
                    onClicked: AppController.detectSpeakers(root.targetVideoId)
                }
            }
        }

        // List of identified characters
        ListView {
            id: speakerListView
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.minimumHeight: 200
            visible: root.speakerProfiles.length > 0
            clip: true
            spacing: Theme.space8
            model: root.speakerProfiles
            ScrollBar.vertical: ScrollBar {
                policy: speakerListView.contentHeight > speakerListView.height ? ScrollBar.AsNeeded : ScrollBar.AlwaysOff
            }

                delegate: Rectangle {
                    id: speakerDelegate
                    required property var modelData
                    required property int index

                    readonly property string speakerId: String(modelData.speakerId || "")
                    readonly property string gender: String(modelData.gender || "male")
                    readonly property string assignedVoice: String(root.draftMapping[speakerId] || modelData.assignedVoice || modelData.defaultVoice || "")

                    width: speakerListView.width
                    implicitHeight: cardLayout.implicitHeight + Theme.space16
                    radius: Theme.radius
                    color: Theme.surfaceElevated
                    border.width: 1
                    border.color: Theme.divider

                    ColumnLayout {
                        id: cardLayout
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.top: parent.top
                        anchors.margins: Theme.space8
                        spacing: Theme.space8

                        // Top row: Avatar + Name + Meta + Voice Combo + Preview Button
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: Theme.space8

                            // Gender Badge
                            Rectangle {
                                Layout.preferredWidth: 32
                                Layout.preferredHeight: 32
                                radius: Theme.radiusSmall
                                color: speakerDelegate.gender === "female" ? "#341738" : "#142540"
                                border.width: 1
                                border.color: speakerDelegate.gender === "female" ? "#db2777" : "#2563eb"

                                Text {
                                    anchors.centerIn: parent
                                    text: speakerDelegate.gender === "female" ? "👩" : "👨"
                                    font.pixelSize: 16
                                }
                            }

                            // Speaker Label & Description
                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 2

                                Text {
                                    text: String(speakerDelegate.modelData.label || speakerDelegate.speakerId)
                                    color: Theme.text
                                    font.family: Theme.fontFamily
                                    font.pixelSize: TypeScale.control
                                    font.weight: Font.DemiBold
                                }

                                Text {
                                    text: String(speakerDelegate.modelData.description || "")
                                    color: Theme.textMuted
                                    font.family: Theme.fontFamily
                                    font.pixelSize: TypeScale.metadata
                                }
                            }

                            // Voice Selector ComboBox
                            StudioComboBox {
                                Layout.preferredWidth: 200
                                model: root.voiceModel
                                textRole: "label"
                                valueRole: "voice"
                                currentIndex: {
                                    const current = speakerDelegate.assignedVoice;
                                    for (let i = 0; i < model.length; ++i) {
                                        if (model[i].voice === current)
                                            return i;
                                    }
                                    return 0;
                                }
                                onActivated: {
                                    const newMap = Object.assign({}, root.draftMapping);
                                    newMap[speakerDelegate.speakerId] = currentValue;
                                    root.draftMapping = newMap;
                                }
                            }

                            // Preview Button
                            StudioButton {
                                Layout.preferredWidth: 34
                                Layout.preferredHeight: 32
                                text: ""
                                iconName: "play"
                                variant: "secondary"
                                Accessible.name: qsTr("Nghe thử giọng")
                                onClicked: root.previewVoice(speakerDelegate.assignedVoice)
                            }
                        }

                        // Sample line quote row
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: Theme.space8
                            visible: String(speakerDelegate.modelData.sampleText || "").length > 0

                            Text {
                                text: "💬"
                                font.pixelSize: 12
                                opacity: 0.6
                            }

                            Text {
                                Layout.fillWidth: true
                                text: "\"" + String(speakerDelegate.modelData.sampleText || "") + "\""
                                color: Theme.textSubtle
                                font.family: Theme.fontFamily
                                font.pixelSize: TypeScale.metadata
                                font.italic: true
                                elide: Text.ElideRight
                                maximumLineCount: 1
                            }
                        }
                    }
                }
            }
        }

    footerActions: [
        StudioButton {
            text: qsTr("Hủy")
            variant: "secondary"
            onClicked: root.close()
        },
        StudioButton {
            text: qsTr("Lưu & Áp dụng")
            iconName: "play"
            variant: "primary"
            enabled: root.speakerProfiles.length > 0
            onClicked: root.saveAndApply()
        }
    ]
}
