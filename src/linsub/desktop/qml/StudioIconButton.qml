import "."

IconButton {
    property string iconName: ""

    controlSize: 30
    glyph: IconCatalog.glyph(iconName)
    showToolTip: toolTipText.length > 0
}
