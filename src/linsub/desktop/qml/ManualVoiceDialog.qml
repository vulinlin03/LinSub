pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Layouts
import QtMultimedia
import "."

AppDialog {
    id: root

    property bool replacingVoice: false
    property string draftProvider: "omnivoice"
    property string draftVoice: ""
    property string draftSpeakerMode: "single"
    property string globalProvider: "omnivoice"
    property string globalVoice: ""
    property string openedVideoId: ""
    property var voiceModel: []
    property string draftMaleVoice: ""
    property string draftFemaleVoice: ""
    property var maleVoiceModel: []
    property var femaleVoiceModel: []
    property string testingVoice: ""

    DefaultAudioOutput {
        id: testAudioOutput
        volume: 1.0
    }

    MediaPlayer {
        id: testAudioPlayer
        audioOutput: testAudioOutput
        onMediaStatusChanged: {
            if (mediaStatus === MediaPlayer.EndOfMedia) {
                stop();
                root.testingVoice = "";
            }
        }
    }

    function playVoiceTest(voice) {
        if (!voice) return;
        if (testingVoice === voice && testAudioPlayer.playbackState === MediaPlayer.PlayingState) {
            testAudioPlayer.stop();
            testingVoice = "";
            return;
        }
        testAudioPlayer.stop();
        testingVoice = voice;
        AppController.previewVoiceSample(root.draftProvider, voice, AppController.manualVoiceLanguage);
        if (AppController.audioPreviewState === "ready" && AppController.audioPreviewSource.length > 0) {
            testAudioPlayer.source = AppController.audioPreviewSource;
            testAudioPlayer.play();
        }
    }

    Connections {
        target: AppController
        function onAudioPreviewChanged() {
            if (root.testingVoice.length > 0 && AppController.audioPreviewState === "ready" && AppController.audioPreviewSource.length > 0) {
                testAudioPlayer.source = AppController.audioPreviewSource;
                testAudioPlayer.play();
            }
        }
    }

    signal confirmed(string provider, string voice, string scope, string segmentId, string speakerMode, var speakerOverrides)
    signal cloneRequested()
    signal speakerMappingRequested()

    title: replacingVoice ? qsTr("Đổi hoặc tạo lại giọng") : qsTr("Tạo giọng đọc")
    preferredWidth: 560
    maximumHeight: 680

    function firstAvailableVoice(options) {
        for (let index = 0; index < options.length; ++index) {
            if (options[index].available === undefined || options[index].available !== false)
                return String(options[index].voice || "");
        }
        return "";
    }

    function findVoiceIndex(options, targetVoice) {
        const t = String(targetVoice || "");
        for (let i = 0; i < options.length; ++i) {
            if (String(options[i].voice || "") === t)
                return i;
        }
        return 0;
    }

    function refreshVoices(preferredVoice) {
        const updatedOptions = AppController.manualVoiceOptionsForProvider(draftProvider);
        if (JSON.stringify(voiceModel) !== JSON.stringify(updatedOptions))
            voiceModel = updatedOptions;
        const requested = String(preferredVoice || "");
        for (let index = 0; index < voiceModel.length; ++index) {
            if (String(voiceModel[index].voice || "") === requested
                    && (voiceModel[index].available === undefined || voiceModel[index].available !== false)) {
                draftVoice = requested;
                break;
            }
        }
        if (!draftVoice)
            draftVoice = firstAvailableVoice(voiceModel);

        maleVoiceModel = AppController.maleVoiceOptionsForProvider(draftProvider);
        femaleVoiceModel = AppController.femaleVoiceOptionsForProvider(draftProvider);

        if (!draftMaleVoice && maleVoiceModel.length > 0)
            draftMaleVoice = String(maleVoiceModel[0].voice || "");
        if (!draftFemaleVoice && femaleVoiceModel.length > 0)
            draftFemaleVoice = String(femaleVoiceModel[0].voice || "");
    }

    function openForVoice(hasVoice, configuration) {
        openedVideoId = String(AppController.selectedVideoId || "");
        replacingVoice = Boolean(hasVoice);
        globalProvider = String(configuration.globalProvider || configuration.provider || "omnivoice");
        globalVoice = String(configuration.globalVoice || configuration.voice || "");
        draftSpeakerMode = String(configuration.speakerMode || "single");
        draftProvider = globalProvider;
        const spkOverrides = configuration.speakerVoiceOverrides || {};
        draftMaleVoice = String(spkOverrides.male || configuration.speakerMaleVoice
            || (draftProvider.indexOf("vieneu") === 0 ? "vieneu:Hải Đăng" : "omnivoice:male"));
        draftFemaleVoice = String(spkOverrides.female || configuration.speakerFemaleVoice
            || (draftProvider.indexOf("vieneu") === 0 ? "vieneu:Trúc Ly" : "omnivoice:female"));
        refreshVoices(globalVoice);
        open();
    }

    function selectCloneReference() {
        draftSpeakerMode = "single";
        const configuration = AppController.manualVoiceConfiguration("");
        draftProvider = String(configuration.globalProvider || AppController.ttsProvider);
        globalProvider = draftProvider;
        globalVoice = String(configuration.globalVoice || AppController.ttsVoice);
        refreshVoices(globalVoice);
    }

    onClosed: {
        testAudioPlayer.stop();
        root.testingVoice = "";
        voicePicker.stopPreview();
    }

    Connections {
        target: AppController

        function onSelectedVideoChanged() {
            if (!root.opened)
                return;
            if (String(AppController.selectedVideoId || "") !== root.openedVideoId) {
                root.close();
                return;
            }
            // Refresh availability while preserving the user's draft. Only
            // an explicit Apply action in the recorder chooses Clone.
            root.refreshVoices(root.draftVoice);
        }
    }

    ColumnLayout {
        Layout.fillWidth: true
        spacing: Theme.space16

        GridLayout {
            Layout.fillWidth: true
            columns: 1
            columnSpacing: Theme.space12
            rowSpacing: Theme.space8

            SettingLabel { text: qsTr("Công cụ") }
            StudioComboBox {
                Layout.fillWidth: true
                model: AppController.ttsProviderOptions
                textRole: "label"
                valueRole: "provider"
                currentIndex: {
                    for (let i = 0; i < model.length; ++i) {
                        if (model[i].provider === root.draftProvider)
                            return i;
                    }
                    return 0;
                }
                onActivated: {
                    if (!AppController.validateTtsProviderForLanguage(AppController.manualVoiceLanguage, currentValue)) {
                        currentIndex = Qt.binding(function() {
                            for (let i = 0; i < model.length; ++i) {
                                if (model[i].provider === root.draftProvider)
                                    return i;
                            }
                            return 0;
                        });
                        return;
                    }
                    root.draftProvider = currentValue;
                    root.draftSpeakerMode = "single";
                    root.refreshVoices(root.draftVoice);
                }
            }

            SettingLabel {
                Layout.fillWidth: true
                text: qsTr("Chế độ phân vai")
                helpText: qsTr("Đơn giọng: dùng 1 giọng cho video. Phân vai: AI tự động phân vai tất cả nhân vật Nam nói 1 giọng và nhân vật Nữ nói 1 giọng.")
            }

            SegmentedControl {
                Layout.fillWidth: true
                currentValue: root.draftSpeakerMode
                options: [
                    { value: "single", label: qsTr("👤 Đơn giọng (1 người)") },
                    { value: "multiple", label: qsTr("👥 Phân vai Nam & Nữ") }
                ]
                onActivated: function(value) {
                    root.draftSpeakerMode = value;
                }
            }

            SettingLabel {
                Layout.fillWidth: true
                visible: root.draftSpeakerMode === "single"
                text: qsTr("Giọng đọc")
                helpText: qsTr("Chọn giọng áp dụng cho video.")
            }

            Text {
                Layout.fillWidth: true
                visible: root.draftProvider === "edge" && root.draftSpeakerMode === "single"
                text: qsTr("Cần Internet. Nội dung phụ đề được gửi tới Microsoft để tạo giọng.")
                color: Theme.textMuted
                font.pixelSize: TypeScale.metadata
                wrapMode: Text.Wrap
            }

            VoicePicker {
                id: voicePicker
                Layout.fillWidth: true
                visible: root.draftSpeakerMode === "single"
                model: root.voiceModel
                currentValue: root.draftVoice
                allowMultipleSpeakers: false
                allowVoiceClone: root.draftProvider.indexOf("omnivoice") === 0 || root.draftProvider.indexOf("vieneu") === 0
                onCloneRequested: root.cloneRequested()
                previewEnabled: true
                previewSource: AppController.audioPreviewSource
                previewState: AppController.audioPreviewState
                onSelected: function(voice) {
                    root.draftVoice = voice;
                }
                onPreviewRequested: function(voice) {
                    root.playVoiceTest(voice);
                }
            }

            Rectangle {
                Layout.fillWidth: true
                implicitHeight: dualVoiceColumn.implicitHeight + Theme.space24
                visible: root.draftSpeakerMode === "multiple"
                radius: Theme.radius
                color: Theme.surfaceElevated
                border.width: 1
                border.color: Theme.interactiveOutline

                ColumnLayout {
                    id: dualVoiceColumn
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.top: parent.top
                    anchors.margins: Theme.space12
                    spacing: Theme.space10

                    Text {
                        text: qsTr("Phân vai giọng đọc Nam & Nữ")
                        color: Theme.text
                        font.family: Theme.fontFamily
                        font.pixelSize: TypeScale.control
                        font.weight: Font.DemiBold
                    }

                    Text {
                        text: qsTr("Tất cả nhân vật nam nói 1 giọng và tất cả nhân vật nữ nói 1 giọng.")
                        color: Theme.textMuted
                        font.family: Theme.fontFamily
                        font.pixelSize: TypeScale.metadata
                        wrapMode: Text.Wrap
                        Layout.fillWidth: true
                    }

                    // Male voice row
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Theme.space8

                        Rectangle {
                            Layout.preferredWidth: 32
                            Layout.preferredHeight: 32
                            radius: Theme.radiusSmall
                            color: "#142540"
                            border.width: 1
                            border.color: "#2563eb"
                            Text {
                                anchors.centerIn: parent
                                text: "👨"
                                font.pixelSize: 16
                            }
                        }

                        Text {
                            text: qsTr("Giọng Nam:")
                            color: Theme.text
                            font.family: Theme.fontFamily
                            font.pixelSize: TypeScale.control
                            font.weight: Font.DemiBold
                            Layout.preferredWidth: 76
                        }

                        StudioComboBox {
                            Layout.fillWidth: true
                            model: root.maleVoiceModel
                            textRole: "label"
                            valueRole: "voice"
                            currentIndex: root.findVoiceIndex(model, root.draftMaleVoice)
                            displayText: currentIndex >= 0 && currentIndex < count ? textAt(currentIndex) : (model && model.length > 0 ? String(model[0].label || "") : "")
                            onActivated: {
                                root.draftMaleVoice = currentValue;
                                root.playVoiceTest(currentValue);
                            }
                        }

                        StudioButton {
                            iconName: root.testingVoice === root.draftMaleVoice && testAudioPlayer.playbackState === MediaPlayer.PlayingState ? "pause" : "play"
                            text: root.testingVoice === root.draftMaleVoice && testAudioPlayer.playbackState === MediaPlayer.PlayingState ? qsTr("Dừng") : qsTr("Nghe thử")
                            variant: root.testingVoice === root.draftMaleVoice && testAudioPlayer.playbackState === MediaPlayer.PlayingState ? "primary" : "secondary"
                            onClicked: {
                                root.playVoiceTest(root.draftMaleVoice);
                            }
                        }
                    }

                    // Female voice row
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Theme.space8

                        Rectangle {
                            Layout.preferredWidth: 32
                            Layout.preferredHeight: 32
                            radius: Theme.radiusSmall
                            color: "#341738"
                            border.width: 1
                            border.color: "#db2777"
                            Text {
                                anchors.centerIn: parent
                                text: "👩"
                                font.pixelSize: 16
                            }
                        }

                        Text {
                            text: qsTr("Giọng Nữ:")
                            color: Theme.text
                            font.family: Theme.fontFamily
                            font.pixelSize: TypeScale.control
                            font.weight: Font.DemiBold
                            Layout.preferredWidth: 76
                        }

                        StudioComboBox {
                            Layout.fillWidth: true
                            model: root.femaleVoiceModel
                            textRole: "label"
                            valueRole: "voice"
                            currentIndex: root.findVoiceIndex(model, root.draftFemaleVoice)
                            displayText: currentIndex >= 0 && currentIndex < count ? textAt(currentIndex) : (model && model.length > 0 ? String(model[0].label || "") : "")
                            onActivated: {
                                root.draftFemaleVoice = currentValue;
                                root.playVoiceTest(currentValue);
                            }
                        }

                        StudioButton {
                            iconName: root.testingVoice === root.draftFemaleVoice && testAudioPlayer.playbackState === MediaPlayer.PlayingState ? "pause" : "play"
                            text: root.testingVoice === root.draftFemaleVoice && testAudioPlayer.playbackState === MediaPlayer.PlayingState ? qsTr("Dừng") : qsTr("Nghe thử")
                            variant: root.testingVoice === root.draftFemaleVoice && testAudioPlayer.playbackState === MediaPlayer.PlayingState ? "primary" : "secondary"
                            onClicked: {
                                root.playVoiceTest(root.draftFemaleVoice);
                            }
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
        DeferredActionButton {
            readyText: qsTr("Tạo giọng")
            iconName: "play"
            variant: "primary"
            actionEnabled: root.draftVoice.length > 0
            actionContext: ({ videoId: root.openedVideoId, provider: root.draftProvider,
                voice: root.draftVoice, speakerMode: root.draftSpeakerMode,
                maleVoice: root.draftMaleVoice, femaleVoice: root.draftFemaleVoice })
            onExecute: context => {
                if (!context || context.videoId !== root.openedVideoId
                        || context.videoId !== String(AppController.selectedVideoId || ""))
                    return;
                const spkOverrides = context.speakerMode === "multiple"
                    ? { "male": context.maleVoice, "female": context.femaleVoice }
                    : {};
                root.confirmed(
                    context.provider,
                    context.voice,
                    "all",
                    "",
                    context.speakerMode,
                    spkOverrides
                );
                root.close();
            }
        }
    ]
}
