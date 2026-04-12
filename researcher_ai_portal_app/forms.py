from __future__ import annotations

from django import forms
from django_svelte_jsoneditor.widgets import SvelteJSONEditorWidget


class ComponentJSONForm(forms.Form):
    """Editable JSON payload for one workflow component."""

    component_json = forms.JSONField(
        required=False,
        widget=SvelteJSONEditorWidget(
            props={
                "mode": "tree",
                "mainMenuBar": True,
                "navigationBar": True,
                "statusBar": True,
            },
            allow_file_import=True,
        ),
    )


_PLOT_TYPE_CHOICES = [
    ("bar", "bar"),
    ("stacked_bar", "stacked_bar"),
    ("grouped_bar", "grouped_bar"),
    ("scatter", "scatter"),
    ("bubble", "bubble"),
    ("line", "line"),
    ("heatmap", "heatmap"),
    ("violin", "violin"),
    ("box", "box"),
    ("volcano", "volcano"),
    ("tsne", "tsne"),
    ("umap", "umap"),
    ("venn", "venn"),
    ("upset", "upset"),
    ("image", "image"),
    ("other", "other"),
]

_PLOT_CATEGORY_CHOICES = [
    ("", "(auto)"),
    ("categorical", "categorical"),
    ("relational", "relational"),
    ("distribution", "distribution"),
    ("matrix", "matrix"),
    ("genomic", "genomic"),
    ("dimensionality", "dimensionality"),
    ("flow", "flow"),
    ("image", "image"),
    ("composite", "composite"),
]

_AXIS_SCALE_CHOICES = [
    ("", "(auto)"),
    ("linear", "linear"),
    ("categorical", "categorical"),
    ("log2", "log2"),
    ("log10", "log10"),
    ("ln", "ln"),
    ("symlog", "symlog"),
    ("reversed", "reversed"),
]


class FigureGroundTruthForm(forms.Form):
    """Quick UI form to inject figure-level ground-truth corrections."""

    figure_id = forms.ChoiceField(choices=(), required=True)
    panel_label = forms.CharField(required=True, max_length=16, initial="A")
    plot_type = forms.ChoiceField(choices=_PLOT_TYPE_CHOICES, required=True, initial="other")
    plot_category = forms.ChoiceField(choices=_PLOT_CATEGORY_CHOICES, required=False)
    title_override = forms.CharField(required=False, max_length=240)
    caption_override = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))
    x_axis_label = forms.CharField(required=False, max_length=240)
    x_axis_scale = forms.ChoiceField(choices=_AXIS_SCALE_CHOICES, required=False)
    y_axis_label = forms.CharField(required=False, max_length=240)
    y_axis_scale = forms.ChoiceField(choices=_AXIS_SCALE_CHOICES, required=False)
    description = forms.CharField(required=False, max_length=500)
    mark_uncertain = forms.BooleanField(required=False, initial=False)

    def __init__(self, *args, figure_ids: list[str] | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        choices = [(fid, fid) for fid in (figure_ids or []) if fid]
        if not choices:
            choices = [("Figure 1", "Figure 1")]
        self.fields["figure_id"].choices = choices


class MethodStepCorrectionForm(forms.Form):
    """Structured form to correct one assay step in the methods payload."""

    assay_index = forms.IntegerField(min_value=0, required=True)
    step_index = forms.IntegerField(min_value=0, required=True)
    description = forms.CharField(required=False, max_length=1000)
    software = forms.CharField(required=False, max_length=240)
    software_version = forms.CharField(required=False, max_length=240)
    input_data = forms.CharField(required=False, max_length=500)
    output_data = forms.CharField(required=False, max_length=500)
    parameters = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))
    code_reference = forms.CharField(required=False, max_length=500)
