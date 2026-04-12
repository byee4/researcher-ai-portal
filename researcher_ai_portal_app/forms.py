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
    parameters = forms.JSONField(
        required=False,
        initial=dict,
        widget=SvelteJSONEditorWidget(
            props={
                "mode": "tree",
                "mainMenuBar": False,
                "navigationBar": False,
                "statusBar": False,
            },
            allow_file_import=False,
        ),
    )
    code_reference = forms.CharField(required=False, max_length=500)
    inferred_stage_name = forms.CharField(required=False, max_length=240)
    resolved_warning_indices = forms.CharField(required=False, max_length=500)
    inferred_stage_warning_index = forms.IntegerField(required=False, min_value=0)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["description"].widget.attrs.update(
            {"placeholder": "e.g., Align FASTQ reads to hg38 and sort BAM output"}
        )
        self.fields["software"].widget.attrs.update(
            {"placeholder": "e.g., STAR, salmon, featureCounts"}
        )
        self.fields["software_version"].widget.attrs.update(
            {
                "placeholder": "e.g., 2.7.11b",
                "list": "method-version-format-options",
            }
        )
        self.fields["input_data"].widget.attrs.update(
            {
                "placeholder": "e.g., FASTQ.gz, BAM, count matrix TSV",
                "list": "method-input-format-options",
            }
        )
        self.fields["output_data"].widget.attrs.update(
            {
                "placeholder": "e.g., sorted BAM, quant.sf, DE table CSV",
                "list": "method-output-format-options",
            }
        )
        self.fields["parameters"].widget.attrs.update(
            {"title": "Parameters must be a JSON dictionary"}
        )
        self.fields["code_reference"].widget.attrs.update(
            {"placeholder": "e.g., nf-core/rnaseq@3.14.0 or https://github.com/org/repo"}
        )

    def clean_parameters(self) -> dict[str, str]:
        value = self.cleaned_data.get("parameters")
        if value in (None, ""):
            return {}
        if not isinstance(value, dict):
            raise forms.ValidationError("Parameters must be a JSON object/dictionary.")
        cleaned: dict[str, str] = {}
        for key, val in value.items():
            cleaned[str(key)] = "" if val is None else str(val)
        return cleaned


_DATASET_SOURCE_CHOICES = [
    ("geo", "GEO"),
    ("sra", "SRA"),
    ("encode", "ENCODE"),
    ("supplementary", "Supplementary"),
    ("other", "Other"),
]


class DatasetCorrectionForm(forms.Form):
    """Structured form to correct one dataset record from the datasets step."""

    dataset_index = forms.IntegerField(min_value=0, required=True)
    accession = forms.CharField(required=True, max_length=160)
    source = forms.ChoiceField(choices=_DATASET_SOURCE_CHOICES, required=True, initial="other")
    title = forms.CharField(required=False, max_length=500)
    organism = forms.CharField(required=False, max_length=240)
    experiment_type = forms.CharField(required=False, max_length=240)
    summary = forms.CharField(required=False, max_length=5000, widget=forms.Textarea(attrs={"rows": 4}))
    primary_url = forms.URLField(required=False, max_length=1000, assume_scheme="https")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["accession"].widget.attrs.update(
            {"placeholder": "e.g., GSE314176, SRP123456, or NO_DATASET_REPORTED"}
        )
        self.fields["title"].widget.attrs.update(
            {"placeholder": "Short dataset title for human readers"}
        )
        self.fields["organism"].widget.attrs.update(
            {"placeholder": "e.g., Homo sapiens"}
        )
        self.fields["experiment_type"].widget.attrs.update(
            {"placeholder": "e.g., RNA-seq, CLIP-seq, ATAC-seq"}
        )
        self.fields["summary"].widget.attrs.update(
            {"placeholder": "Plain-English description of what this dataset contains"}
        )
        self.fields["primary_url"].widget.attrs.update(
            {"placeholder": "https://... link to GEO/SRA/portal landing page"}
        )

    def clean_accession(self) -> str:
        value = str(self.cleaned_data.get("accession") or "").strip()
        if not value:
            raise forms.ValidationError("Accession is required.")
        return value
