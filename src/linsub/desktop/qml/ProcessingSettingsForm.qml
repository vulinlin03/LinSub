import QtQuick
import QtQuick.Layouts
import QtMultimedia
import "."

GridLayout {
    id: root

    property bool editable: true
    property string outputFormat: "keep_ratio"
    signal outputFormatEdited(string value)
    signal outputSpeedEdited(real value)
    property bool cpuOnly: false
    property bool hasSource: false
    property bool showCloneAction: false
    property bool showMusicPlaybackSettings: true
    property real availableHeight: 0
    readonly property real voiceSpacing: Math.max(Theme.space6, Math.min(Theme.space16,
        Theme.space6 + (availableHeight - 500) / 20))
    readonly property real mediaSpacing: Math.max(3, Math.min(Theme.space16,
        3 + (availableHeight - 500) / 24))
    property string speechRecognitionModel: "small"
    property string translationModel: "q4"
    property var speechRecognitionOptions: []
    property int speechRecognitionIndex: 0
    property string targetLanguage: "vi"
    property var targetLanguageOptions: []
    property string ttsProvider: "omnivoice"
    property real outputSpeed: 1.0
    property var ttsProviderOptions: []
    property int ttsProviderIndex: 0
    property string ttsVoice: ""
    property var ttsVoiceOptions: []
    property string voicePreviewSource: ""
    property string voicePreviewState: "idle"
    property string speakerMode: "single"
    property bool removeOriginalSubtitles: true
    property string subtitleRemovalMode: "patch"
    property bool enableAudioSeparation: true
    property string backgroundMusicPath: ""
    property bool backgroundMusicLoop: true
    property bool audioDuckingEnabled: false
    property int audioDuckingReductionDb: -12
    property string watermarkText: ""
    property string watermarkKind: "text"
    property string watermarkImagePath: ""
    property string watermarkVideoPath: ""
    property bool allowSpeakerMapping: true
    property string speakerMaleVoice: ""
    property string speakerFemaleVoice: ""
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
        AppController.previewVoiceSample(root.ttsProvider, voice, root.targetLanguage);
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

    signal speechRecognitionEdited(string value)
    signal translationModelEdited(string value)
    signal targetLanguageEdited(string value)
    signal ttsProviderEdited(string value)
    signal ttsVoiceEdited(string value)
    signal ttsVoicePreviewRequested(string value)
    signal cloneVoiceRequested()
    signal speakerModeEdited(string value)
    signal speakerMaleVoiceEdited(string value)
    signal speakerFemaleVoiceEdited(string value)
    signal speakerMappingRequested()
    signal removeOriginalSubtitlesEdited(bool value)
    signal subtitleRemovalModeEdited(string value)
    signal subtitleLayoutRequested()
    signal sourceCoverRequested()
    signal audioSeparationEdited(bool value)
    signal audioMixRequested()
    signal backgroundMusicFileRequested()
    signal backgroundMusicLinkRequested()
    signal backgroundMusicClearRequested()
    signal backgroundMusicLoopEdited(bool value)
    signal audioDuckingEdited(bool value)
    signal audioDuckingReductionEdited(int value)
    signal watermarkKindEdited(string value)
    signal watermarkRequested()

    columns: width >= 650 ? 2 : 1
    columnSpacing: Theme.space20
    rowSpacing: Theme.space16

    Item {
        Layout.fillWidth: true
        Layout.fillHeight: root.columns === 2
        Layout.alignment: Qt.AlignTop
        implicitHeight: voiceColumn.implicitHeight

        ColumnLayout {
            id: voiceColumn
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.leftMargin: Theme.space4
            anchors.rightMargin: Theme.space4
            spacing: root.voiceSpacing

            Text {
                text: qsTr("Ngôn ngữ và giọng")
                color: Theme.text
                font.pixelSize: Theme.body
                font.weight: Font.DemiBold
                textFormat: Text.PlainText
            }

            Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: Theme.divider }

            Text {
                Layout.fillWidth: true
                text: AppController.processingDevice === "gpu"
                ? qsTr("Chế độ GPU: có thể chọn model GPU hoặc CPU.")
                    : qsTr("Chế độ CPU: model GPU chưa khả dụng. Đổi bộ xử lý trong Cài đặt → Chung để sử dụng GPU.")
                color: Theme.textMuted
                font.pixelSize: TypeScale.metadata
                wrapMode: Text.Wrap
                textFormat: Text.PlainText
            }

            SettingLabel {
                Layout.fillWidth: true
                text: qsTr("Nhận dạng giọng nói")
                helpText: qsTr("Turbo cần GPU NVIDIA. Small dùng được trên CPU và GPU.")
            }
            StudioComboBox {
                Layout.fillWidth: true
                enabled: root.editable
                textRole: "label"
                valueRole: "value"
                model: root.speechRecognitionOptions
                currentIndex: root.speechRecognitionIndex
                onActivated: {
                    root.speechRecognitionEdited(currentValue);
                    currentIndex = Qt.binding(function() { return root.speechRecognitionIndex; });
                }
            }

            SettingLabel {
                Layout.fillWidth: true
                text: qsTr("Model dịch")
                helpText: qsTr("HY-MT2 chạy cục bộ. Gemini cần Internet và API key riêng; Google có thể tính phí theo mức sử dụng.")
            }
            TranslationModelCombo {
                Layout.fillWidth: true
                enabled: root.editable && !AppController.isProcessing
                selectedModel: root.translationModel
                onEdited: function(value) { root.translationModelEdited(value) }
            }

            SettingLabel {
                Layout.fillWidth: true
                text: qsTr("Dịch sang")
            }
            SearchableLanguageCombo {
                Layout.fillWidth: true
                Layout.preferredHeight: 32
                enabled: root.editable
                options: root.targetLanguageOptions
                selectedCode: root.targetLanguage
                onSelected: function(code) { root.targetLanguageEdited(code) }
            }

            SettingLabel {
                Layout.fillWidth: true
                text: qsTr("Công cụ giọng đọc")
            }
            StudioComboBox {
                Layout.fillWidth: true
                enabled: root.editable
                model: root.ttsProviderOptions
                textRole: "label"
                valueRole: "provider"
                currentIndex: root.ttsProviderIndex
                onActivated: {
                    root.ttsProviderEdited(currentValue);
                    currentIndex = Qt.binding(function() { return root.ttsProviderIndex; });
                }
            }
            SettingLabel {
                Layout.fillWidth: true
                text: qsTr("Chế độ phân vai")
                helpText: qsTr("Đơn giọng: dùng 1 giọng cho video. Phân vai: AI tự động phân vai tất cả nhân vật Nam nói 1 giọng và nhân vật Nữ nói 1 giọng.")
            }

            SegmentedControl {
                Layout.fillWidth: true
                enabled: root.editable
                currentValue: root.speakerMode
                options: [
                    { value: "single", label: qsTr("👤 Đơn giọng (1 người)") },
                    { value: "multiple", label: qsTr("👥 Phân vai Nam & Nữ") }
                ]
                onActivated: function(value) {
                    root.speakerModeEdited(value);
                }
            }

            SettingLabel {
                Layout.fillWidth: true
                visible: root.speakerMode === "single"
                text: qsTr("Giọng đọc")
                helpText: qsTr("Chọn giọng áp dụng cho toàn bộ video.")
            }
            Text {
                Layout.fillWidth: true
                visible: root.ttsProvider === "edge" && root.speakerMode === "single"
                text: qsTr("Cần Internet. Nội dung phụ đề được gửi tới Microsoft để tạo giọng.")
                color: Theme.textMuted
                font.pixelSize: TypeScale.metadata
                wrapMode: Text.Wrap
            }
            VoicePicker {
                Layout.fillWidth: true
                visible: root.speakerMode === "single"
                enabled: root.editable
                model: root.ttsVoiceOptions
                currentValue: root.ttsVoice
                allowMultipleSpeakers: false
                allowVoiceClone: root.showCloneAction && (root.ttsProvider.indexOf("omnivoice") === 0 || root.ttsProvider.indexOf("vieneu") === 0)
                previewSource: root.voicePreviewSource
                previewState: root.voicePreviewState
                onSelected: function(voice) {
                    root.ttsVoiceEdited(voice);
                }
                onPreviewRequested: function(voice) { root.playVoiceTest(voice); }
                onCloneRequested: root.cloneVoiceRequested()
            }

            Rectangle {
                Layout.fillWidth: true
                implicitHeight: dualVoiceColumn.implicitHeight + Theme.space16
                visible: root.speakerMode === "multiple"
                radius: Theme.radius
                color: Theme.surfaceElevated
                border.width: 1
                border.color: Theme.interactiveOutline

                ColumnLayout {
                    id: dualVoiceColumn
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.top: parent.top
                    anchors.margins: Theme.space8
                    spacing: Theme.space6

                    Text {
                        text: qsTr("👥 Phân vai giọng theo giới tính:")
                        color: Theme.interactiveHover
                        font.family: Theme.fontFamily
                        font.pixelSize: TypeScale.control
                        font.weight: Font.DemiBold
                    }

                    // Male voice row
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Theme.space6

                        Rectangle {
                            Layout.preferredWidth: 26
                            Layout.preferredHeight: 26
                            radius: Theme.radiusSmall
                            color: "#142540"
                            border.width: 1
                            border.color: "#2563eb"
                            Text {
                                anchors.centerIn: parent
                                text: "👨"
                                font.pixelSize: 14
                            }
                        }

                        Text {
                            text: qsTr("Giọng Nam:")
                            color: Theme.text
                            font.family: Theme.fontFamily
                            font.pixelSize: TypeScale.control
                            font.weight: Font.DemiBold
                            Layout.preferredWidth: 68
                        }

                        StudioComboBox {
                            Layout.fillWidth: true
                            enabled: root.editable
                            model: AppController.maleVoiceOptionsForProvider(root.ttsProvider)
                            textRole: "label"
                            valueRole: "voice"
                            currentIndex: {
                                for (let i = 0; i < model.length; ++i) {
                                    if (String(model[i].voice || "") === String(root.speakerMaleVoice || ""))
                                        return i;
                                }
                                return 0;
                            }
                            displayText: currentIndex >= 0 && currentIndex < count ? textAt(currentIndex) : (model && model.length > 0 ? String(model[0].label || "") : "")
                            onActivated: {
                                root.speakerMaleVoiceEdited(currentValue);
                                root.playVoiceTest(currentValue);
                                currentIndex = Qt.binding(function() {
                                    for (let i = 0; i < model.length; ++i) {
                                        if (String(model[i].voice || "") === String(root.speakerMaleVoice || ""))
                                            return i;
                                    }
                                    return 0;
                                });
                            }
                        }

                        StudioButton {
                            iconName: root.testingVoice === root.speakerMaleVoice && testAudioPlayer.playbackState === MediaPlayer.PlayingState ? "pause" : "play"
                            text: root.testingVoice === root.speakerMaleVoice && testAudioPlayer.playbackState === MediaPlayer.PlayingState ? qsTr("Dừng") : qsTr("Nghe thử")
                            variant: root.testingVoice === root.speakerMaleVoice && testAudioPlayer.playbackState === MediaPlayer.PlayingState ? "primary" : "secondary"
                            enabled: root.editable
                            onClicked: {
                                root.playVoiceTest(root.speakerMaleVoice);
                            }
                        }
                    }

                    // Female voice row
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Theme.space6

                        Rectangle {
                            Layout.preferredWidth: 26
                            Layout.preferredHeight: 26
                            radius: Theme.radiusSmall
                            color: "#341738"
                            border.width: 1
                            border.color: "#db2777"
                            Text {
                                anchors.centerIn: parent
                                text: "👩"
                                font.pixelSize: 14
                            }
                        }

                        Text {
                            text: qsTr("Giọng Nữ:")
                            color: Theme.text
                            font.family: Theme.fontFamily
                            font.pixelSize: TypeScale.control
                            font.weight: Font.DemiBold
                            Layout.preferredWidth: 68
                        }

                        StudioComboBox {
                            Layout.fillWidth: true
                            enabled: root.editable
                            model: AppController.femaleVoiceOptionsForProvider(root.ttsProvider)
                            textRole: "label"
                            valueRole: "voice"
                            currentIndex: {
                                for (let i = 0; i < model.length; ++i) {
                                    if (String(model[i].voice || "") === String(root.speakerFemaleVoice || ""))
                                        return i;
                                }
                                return 0;
                            }
                            displayText: currentIndex >= 0 && currentIndex < count ? textAt(currentIndex) : (model && model.length > 0 ? String(model[0].label || "") : "")
                            onActivated: {
                                root.speakerFemaleVoiceEdited(currentValue);
                                root.playVoiceTest(currentValue);
                                currentIndex = Qt.binding(function() {
                                    for (let i = 0; i < model.length; ++i) {
                                        if (String(model[i].voice || "") === String(root.speakerFemaleVoice || ""))
                                            return i;
                                    }
                                    return 0;
                                });
                            }
                        }

                        StudioButton {
                            iconName: root.testingVoice === root.speakerFemaleVoice && testAudioPlayer.playbackState === MediaPlayer.PlayingState ? "pause" : "play"
                            text: root.testingVoice === root.speakerFemaleVoice && testAudioPlayer.playbackState === MediaPlayer.PlayingState ? qsTr("Dừng") : qsTr("Nghe thử")
                            variant: root.testingVoice === root.speakerFemaleVoice && testAudioPlayer.playbackState === MediaPlayer.PlayingState ? "primary" : "secondary"
                            enabled: root.editable
                            onClicked: {
                                root.playVoiceTest(root.speakerFemaleVoice);
                            }
                        }
                    }
                }
            }

        }
    }

    Item {
        Layout.fillWidth: true
        Layout.fillHeight: root.columns === 2
        Layout.alignment: Qt.AlignTop
        implicitHeight: mediaColumn.implicitHeight

        ColumnLayout {
            id: mediaColumn
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.leftMargin: Theme.space4
            anchors.rightMargin: Theme.space4
            spacing: root.mediaSpacing

            Text {
                text: qsTr("Hình ảnh và âm thanh")
                color: Theme.text
                font.pixelSize: Theme.body
                font.weight: Font.DemiBold
                textFormat: Text.PlainText
            }

            Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: Theme.divider }

            OutputFrameSettings {
                Layout.fillWidth: true
                compact: true
                editable: root.editable
                outputFormat: root.outputFormat
                showSpeed: true
                outputSpeed: root.outputSpeed
                onSpeedEdited: function(value) { root.outputSpeedEdited(value); }
                onEdited: function(value) { root.outputFormatEdited(value); }
            }

            RowLayout {
                Layout.fillWidth: true
                SettingLabel {
                    Layout.fillWidth: true
                    text: qsTr("Phụ đề gốc")
                }
                StudioButton {
                    objectName: "sourceCoverButton"
                    text: qsTr("Chỉnh vùng che")
                    enabled: root.editable && root.hasSource && root.removeOriginalSubtitles
                    onClicked: root.sourceCoverRequested()
                }
                StudioButton {
                    text: qsTr("Chỉnh phụ đề")
                    enabled: root.editable && root.hasSource
                    onClicked: root.subtitleLayoutRequested()
                }
            }
            SegmentedControl {
                Layout.fillWidth: true
                Layout.preferredHeight: UiMetrics.controlHeight
                enabled: root.editable
                currentValue: root.removeOriginalSubtitles ? "remove" : "keep"
                options: [
                    { "label": qsTr("Che phụ đề gốc"), "value": "remove" },
                    { "label": qsTr("Giữ nguyên video gốc"), "value": "keep" }
                ]
                onActivated: function(value) { root.removeOriginalSubtitlesEdited(value === "remove") }
            }

            RowLayout {
                Layout.fillWidth: true
                visible: root.removeOriginalSubtitles
                spacing: Theme.space8
                SettingLabel {
                    text: qsTr("Cách xóa")
                    helpText: qsTr("Làm mờ làm nhòe chữ được nhận diện. Vá nền dùng vùng ảnh sạch lân cận để lấp chữ.")
                }
                SegmentedControl {
                    Layout.fillWidth: true
                    Layout.preferredHeight: UiMetrics.controlHeight
                    enabled: root.editable
                    currentValue: root.subtitleRemovalMode
                    options: [
                        { "label": qsTr("Làm mờ"), "value": "blur" },
                        { "label": qsTr("Vá nền lân cận"), "value": "patch" }
                    ]
                    onActivated: function(value) { root.subtitleRemovalModeEdited(value) }
                }
            }

            Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: Theme.divider }

            SettingLabel {
                Layout.fillWidth: true
                text: qsTr("Nguồn âm thanh")
            }
            RowLayout {
                Layout.fillWidth: true
                spacing: Theme.space8
                SegmentedControl {
                    Layout.fillWidth: true
                    Layout.preferredHeight: UiMetrics.controlHeight
                    enabled: root.editable
                    currentValue: root.enableAudioSeparation ? "separated" : "original"
                    options: [
                        { "label": qsTr("Giữ âm thanh gốc"), "value": "original" },
                        { "label": qsTr("Tách giọng"), "value": "separated" }
                    ]
                    onActivated: function(value) { root.audioSeparationEdited(value === "separated") }
                }
                StudioButton {
                    text: qsTr("Âm lượng")
                    variant: "secondary"
                    compact: true
                    enabled: root.editable
                    onClicked: root.audioMixRequested()
                }
            }
            Text {
                Layout.fillWidth: true
                visible: root.cpuOnly && root.enableAudioSeparation
                text: qsTr("Tách âm sẽ chậm hơn khi chạy bằng CPU")
                color: Theme.warning
                font.pixelSize: Theme.label
                wrapMode: Text.WordWrap
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: Theme.space8
                SettingLabel {
                    Layout.fillWidth: true
                    text: qsTr("Nhạc nền")
                }
                StudioButton { text: qsTr("Tệp"); enabled: root.editable; onClicked: root.backgroundMusicFileRequested() }
                StudioButton { text: qsTr("Liên kết"); enabled: root.editable; onClicked: root.backgroundMusicLinkRequested() }
                StudioIconButton {
                    visible: root.backgroundMusicPath.length > 0
                    iconName: "delete"
                    toolTipText: qsTr("Bỏ nhạc nền")
                    enabled: root.editable
                    onClicked: root.backgroundMusicClearRequested()
                }
            }

            Text {
                Layout.fillWidth: true
                text: root.backgroundMusicPath.length > 0 ? root.backgroundMusicPath : qsTr("Chưa có nhạc nền")
                color: root.backgroundMusicPath.length > 0 ? Theme.text : Theme.textMuted
                font.pixelSize: Theme.label
                elide: Text.ElideMiddle
            }

            MusicPlaybackSettings {
                Layout.fillWidth: true
                compact: root.availableHeight < 580
                visible: root.showMusicPlaybackSettings
                editable: root.editable
                hasMusic: root.backgroundMusicPath.length > 0
                loopMusic: root.backgroundMusicLoop
                ducking: root.audioDuckingEnabled
                reductionDb: root.audioDuckingReductionDb
                onLoopEdited: function(value) { root.backgroundMusicLoopEdited(value) }
                onDuckingEdited: function(value) { root.audioDuckingEdited(value) }
                onReductionEdited: function(value) { root.audioDuckingReductionEdited(value) }
                onMusicRequired: AppController.appAlertRequested(qsTr("Nhạc nền"), qsTr("Hãy nhập nhạc nền trước."), "info")
            }

            RowLayout {
                Layout.fillWidth: true
                SettingLabel {
                    Layout.fillWidth: true
                    text: qsTr("Watermark")
                }
                StudioButton {
                    objectName: "autoWatermarkEditButton"
                    text: qsTr("Chỉnh sửa")
                    enabled: root.editable
                    onClicked: root.watermarkRequested()
                }
            }
            SegmentedControl {
                Layout.fillWidth: true
                enabled: root.editable
                currentValue: root.watermarkKind
                options: [
                    { "label": qsTr("Chữ"), "value": "text" },
                    { "label": qsTr("Ảnh"), "value": "image" },
                    { "label": qsTr("Video"), "value": "video" }
                ]
                onActivated: function(value) { root.watermarkKindEdited(value) }
            }
            RowLayout {
                Layout.fillWidth: true
                spacing: Theme.space8
                Text {
                    Layout.fillWidth: true
                    text: root.watermarkKind === "image"
                        ? (root.watermarkImagePath.length > 0 ? qsTr("Ảnh watermark") : qsTr("Chưa chọn ảnh"))
                        : root.watermarkKind === "video"
                            ? (root.watermarkVideoPath.length > 0 ? qsTr("Video watermark") : qsTr("Chưa chọn video"))
                            : (root.watermarkText.length > 0 ? root.watermarkText : qsTr("Không có watermark"))
                    color: (root.watermarkKind === "image" && root.watermarkImagePath.length > 0)
                        || (root.watermarkKind === "video" && root.watermarkVideoPath.length > 0)
                        || (root.watermarkKind === "text" && root.watermarkText.length > 0)
                        ? Theme.text : Theme.textMuted
                    font.pixelSize: Theme.label
                    elide: Text.ElideRight
                }
            }
        }
    }
}
