from __future__ import annotations

import argparse
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from src.remote.p0b_utils import LOCAL_REPORTS, pull_report, remote_project_prelude, write_local_report
from src.remote.server_config import ServerConfig
from src.remote.ssh_runner import SSHRunner
from src.utils.io import ensure_parent


REPORTS = [
    "current_data_inventory.md",
    "data_quality_report.md",
    "baseline_results.md",
    "baseline_results.json",
    "backtest_results.md",
    "feature_ablation.md",
    "class_imbalance_strategy_report.md",
    "p0a_status.md",
    "p0a_final_review.md",
    "local_qwen_model_search_report.md",
    "local_qwen_model_candidates.json",
    "remote_qwen_model_search_report.md",
    "remote_qwen_model_candidates.json",
    "qwen_model_upload_report.md",
    "qwen_model_upload_report.json",
    "local_model_manifest.json",
    "remote_model_manifest.json",
    "qwen_remote_model_validation_report.md",
    "qwen_remote_model_validation_report.json",
    "qwen_sft_schema_report.md",
    "qwen_sft_schema_report.json",
    "hf_connectivity_report.md",
    "hf_connectivity_report.json",
    "hardware_report.md",
    "hardware_report.json",
    "p0c1_status.md",
    "p0c1_status.json",
    "qwen_lora_gpu_preflight_dryrun.md",
    "qwen_lora_gpu_preflight_dryrun.json",
    "qwen_lora_gpu_preflight_train.md",
    "qwen_lora_gpu_preflight_train.json",
    "qwen_lora_dataset_report.md",
    "qwen_lora_dataset_report.json",
    "qwen_lora_dryrun_report.md",
    "qwen_lora_dryrun_report.json",
    "qwen_lora_train_report.md",
    "qwen_lora_train_report.json",
    "qwen_lora_eval_report.md",
    "qwen_lora_eval_metrics.json",
    "p0c2_status.md",
    "p0c2_status.json",
    "qwen_candidate_review.md",
    "qwen_candidate_review.json",
    "qwen_error_analysis.md",
    "qwen_error_analysis.json",
    "baseline_qwen_comparison.md",
    "baseline_qwen_comparison.json",
    "step_experience_report.md",
    "step_experience_report.json",
    "forgetting_guard_report.md",
    "forgetting_guard_report.json",
    "p0c3_status.md",
    "p0c3_status.json",
    "p0c3_final_review.md",
    "p0c3_final_review.json",
    "same_split_baseline_eval.md",
    "same_split_baseline_eval.json",
    "same_split_baseline_predictions.jsonl",
    "up_recall_slice_analysis.md",
    "up_recall_slice_analysis.json",
    "qwen_guard_ensemble_eval.md",
    "qwen_guard_ensemble_eval.json",
    "qwen_guard_ensemble_predictions.jsonl",
    "qwen_prompt_evidence_diagnosis.md",
    "qwen_prompt_evidence_diagnosis.json",
    "p0c4a_status.md",
    "p0c4a_status.json",
    "p0c4a_final_review.md",
    "p0c4a_final_review.json",
    "qwen_candidate_v2_dataset_report.md",
    "qwen_candidate_v2_dataset_report.json",
    "qwen_candidate_v2_prompt_plan.md",
    "qwen_candidate_v2_prompt_plan.json",
    "qwen_candidate_v2_training_plan.md",
    "qwen_candidate_v2_training_plan.json",
    "qwen_candidate_v2_eval_plan.md",
    "qwen_candidate_v2_eval_plan.json",
    "p0c4b_prep_status.md",
    "p0c4b_prep_status.json",
    "qwen_lora_v2_dataset_validation_report.md",
    "qwen_lora_v2_dataset_validation_report.json",
    "qwen_lora_v2_gpu_preflight.md",
    "qwen_lora_v2_gpu_preflight.json",
    "qwen_lora_v2_dryrun_report.md",
    "qwen_lora_v2_dryrun_report.json",
    "qwen_lora_v2_train_report.md",
    "qwen_lora_v2_train_report.json",
    "qwen_lora_v2_eval_report.md",
    "qwen_lora_v2_eval_metrics.json",
    "qwen_lora_v2_compare.md",
    "qwen_lora_v2_compare.json",
    "p0c4b_train_status.md",
    "p0c4b_train_status.json",
    "final_model_review.md",
    "final_model_review.json",
    "model_selection_report.md",
    "model_selection_report.json",
    "up_recall_failure_report.md",
    "up_recall_failure_report.json",
    "data_gap_report.md",
    "data_gap_report.json",
    "next_iteration_plan.md",
    "next_iteration_plan.json",
    "final_project_status.md",
    "external_data_inventory.md",
    "external_data_inventory.json",
    "external_data_quality_report.md",
    "external_data_quality_report.json",
    "external_feature_build_report.md",
    "external_feature_build_report.json",
    "baseline_after_data_expansion.md",
    "baseline_after_data_expansion.json",
    "backtest_after_data_expansion.md",
    "backtest_after_data_expansion.json",
    "guard_after_data_expansion.md",
    "guard_after_data_expansion.json",
    "guard_after_data_expansion_predictions.jsonl",
    "p0d_data_expansion_status.md",
    "p0d_data_expansion_status.json",
    "external_dataset_download_report.md",
    "external_dataset_download_report.json",
    "external_dataset_inventory_report.md",
    "external_dataset_inventory_report.json",
    "external_hdx_schema_probe_report.md",
    "external_hdx_schema_probe_report.json",
    "external_hdx_case_staging_report.md",
    "external_hdx_case_staging_report.json",
    "external_data_final_inventory.md",
    "external_data_final_inventory.json",
    "external_hdx_column_mapping_report.md",
    "external_hdx_column_mapping_report.json",
    "external_admin_matching_report.md",
    "external_admin_matching_report.json",
    "external_case_normalization_report.md",
    "external_case_normalization_report.json",
    "external_case_deduplication_report.md",
    "external_case_deduplication_report.json",
    "external_weather_alignment_report.md",
    "external_weather_alignment_report.json",
    "external_panel_build_report.md",
    "external_panel_build_report.json",
    "external_label_quality_report.md",
    "external_label_quality_report.json",
    "external_quality_gate_report.md",
    "external_quality_gate_report.json",
    "external_validation_plan.md",
    "external_validation_plan.json",
    "external_validation_dataset_report.md",
    "external_validation_dataset_report.json",
    "external_baseline_eval.md",
    "external_baseline_eval.json",
    "external_guard_eval.md",
    "external_guard_eval.json",
    "external_qwen_v1_eval.md",
    "external_qwen_v1_eval.json",
    "external_qwen_v2_eval.md",
    "external_qwen_v2_eval.json",
    "external_model_comparison.md",
    "external_model_comparison.json",
    "external_error_analysis.md",
    "external_error_analysis.json",
    "p0e_external_validation_status.md",
    "p0e_external_validation_status.json",
    "p0e_final_review.md",
    "p0e_final_review.json",
    "baseline_artifact_recovery_report.md",
    "baseline_artifact_recovery_report.json",
    "external_baseline_recovery_eval.md",
    "external_baseline_recovery_eval.json",
    "external_model_selection_review.md",
    "external_model_selection_review.json",
    "school_data_request_draft.md",
    "school_data_request_draft.json",
    "school_data_request_draft.docx",
    "v3_training_prerequisites.md",
    "v3_training_prerequisites.json",
    "p0f_status.md",
    "p0f_status.json",
    "v3_retraining_readiness_report.md",
    "v3_retraining_readiness_report.json",
    "p1b_source_registry_report.md",
    "p1b_source_registry_report.json",
    "p1b_download_inventory.md",
    "p1b_download_inventory.json",
    "p1b_prisma_dataset_links.md",
    "p1b_prisma_dataset_links.json",
    "p1b_prisma_query_report.md",
    "p1b_prisma_query_report.json",
    "p1b_source_download_report.md",
    "p1b_source_download_report.json",
    "p1b_admin_population_weather_download_report.md",
    "p1b_admin_population_weather_download_report.json",
    "p1b_mobility_download_report.md",
    "p1b_mobility_download_report.json",
    "external_v3_schema_probe_report.md",
    "external_v3_schema_probe_report.json",
    "external_v3_column_mapping_report.md",
    "external_v3_column_mapping_report.json",
    "external_v3_admin_matching_report.md",
    "external_v3_admin_matching_report.json",
    "external_v3_case_normalization_report.md",
    "external_v3_case_normalization_report.json",
    "external_v3_deduplication_report.md",
    "external_v3_deduplication_report.json",
    "external_v3_weather_alignment_report.md",
    "external_v3_weather_alignment_report.json",
    "external_v3_mobility_report.md",
    "external_v3_mobility_report.json",
    "external_v3_panel_build_report.md",
    "external_v3_panel_build_report.json",
    "external_v3_label_quality_report.md",
    "external_v3_label_quality_report.json",
    "external_v3_role_assignment_report.md",
    "external_v3_role_assignment_report.json",
    "train_blocking_resolution_matrix.md",
    "train_blocking_resolution_matrix.json",
    "manual_data_acquisition_templates.md",
    "manual_data_acquisition_templates.json",
    "outbreak_retraining_design.md",
    "outbreak_retraining_design.json",
    "p1c_status.md",
    "p1c_status.json",
    "drc_health_zone_mapping_report.md",
    "drc_health_zone_mapping_report.json",
    "drc_manual_case_completion_guide.md",
    "uganda_manual_case_completion_guide.md",
    "expert_error_review_queue_report.md",
    "expert_error_review_queue_report.json",
    "preference_pair_annotation_queue_report.md",
    "preference_pair_annotation_queue_report.json",
    "v3_retraining_readiness_after_p1c_report.md",
    "v3_retraining_readiness_after_p1c_report.json",
    "manual_data_completion_package.md",
    "manual_data_completion_package.json",
    "manual_data_completion_package.zip",
    "manual_data_completion_instructions.md",
    "filled_template_validator_summary.json",
    "filled_template_validation_status.md",
    "filled_template_validation_status.json",
    "p1e_status.md",
    "p1e_status.json",
    "outbreak_prediction_task_definition.md",
    "outbreak_prediction_task_definition.json",
    "outbreak_data_requirement_report.md",
    "outbreak_data_requirement_report.json",
    "outbreak_literature_data_survey.md",
    "outbreak_literature_data_survey.json",
    "outbreak_model_training_plan.md",
    "outbreak_model_training_plan.json",
    "p2a_feasibility_status.md",
    "p2a_feasibility_status.json",
    "outbreak_source_registry_report.md",
    "outbreak_source_registry_report.json",
    "outbreak_data_acquisition_report.md",
    "outbreak_data_acquisition_report.json",
    "outbreak_literature_survey_report.md",
    "outbreak_literature_survey_report.json",
    "outbreak_geo_pop_weather_collection_report.md",
    "outbreak_geo_pop_weather_collection_report.json",
    "outbreak_osint_signal_report.md",
    "outbreak_osint_signal_report.json",
    "outbreak_event_table_report.md",
    "outbreak_event_table_report.json",
    "region_week_panel_report.md",
    "region_week_panel_report.json",
    "outbreak_label_quality_report.md",
    "outbreak_label_quality_report.json",
    "outbreak_negative_sampling_report.md",
    "outbreak_negative_sampling_report.json",
    "outbreak_feature_build_report.md",
    "outbreak_feature_build_report.json",
    "outbreak_leakage_check_report.md",
    "outbreak_leakage_check_report.json",
    "outbreak_dataset_quality_report.md",
    "outbreak_dataset_quality_report.json",
    "outbreak_risk_baseline_train_report.md",
    "outbreak_risk_baseline_train_report.json",
    "outbreak_risk_baseline_eval_report.md",
    "outbreak_risk_baseline_eval_report.json",
    "outbreak_leave_one_out_validation_report.md",
    "outbreak_leave_one_out_validation_report.json",
    "outbreak_risk_error_analysis.md",
    "outbreak_risk_error_analysis.json",
    "outbreak_prototype_training_report.md",
    "outbreak_prototype_training_report.json",
    "p2a_status.md",
    "p2a_status.json",
]
P2B_REPORTS = [
    "p2a_prototype_final_review.md",
    "p2a_prototype_final_review.json",
    "outbreak_dataset_audit.md",
    "outbreak_dataset_audit.json",
    "outbreak_external_error_audit.md",
    "outbreak_external_error_audit.json",
    "outbreak_feature_gap_analysis.md",
    "outbreak_feature_gap_analysis.json",
    "outbreak_literature_link_triage.md",
    "outbreak_literature_link_triage.json",
    "outbreak_dataset_expansion_plan.md",
    "outbreak_dataset_expansion_plan.json",
    "outbreak_risk_supervisor_report.md",
    "p2b_status.md",
    "p2b_status.json",
]
TEXT_REPORT_ZIP = "p0a_text_reports.zip"
TEMPLATE_FILES = [
    "drc_2018_2020_case_template.csv",
    "uganda_2022_case_template.csv",
    "expert_error_review_template.csv",
    "preference_pair_template.jsonl",
    "drc_2018_2020_health_zone_case_template.csv",
    "uganda_2022_district_case_template.csv",
    "expert_error_review_queue.csv",
    "preference_pair_annotation_queue.jsonl",
]
MANUAL_COMPLETION_PACKAGE_FILES = [
    "drc_2018_2020_health_zone_case_template.csv",
    "uganda_2022_district_case_template.csv",
    "expert_error_review_queue.csv",
    "preference_pair_annotation_queue.jsonl",
    "manual_data_completion_readme.md",
    "field_dictionary.csv",
]
OPTIONAL_REPORTS = {
    "qwen_lora_gpu_preflight_train.md",
    "qwen_lora_gpu_preflight_train.json",
    "qwen_lora_dryrun_report.md",
    "qwen_lora_dryrun_report.json",
    "qwen_lora_train_report.md",
    "qwen_lora_train_report.json",
    "qwen_lora_eval_report.md",
    "qwen_lora_eval_metrics.json",
    "qwen_candidate_review.md",
    "qwen_candidate_review.json",
    "qwen_error_analysis.md",
    "qwen_error_analysis.json",
    "baseline_qwen_comparison.md",
    "baseline_qwen_comparison.json",
    "step_experience_report.md",
    "step_experience_report.json",
    "forgetting_guard_report.md",
    "forgetting_guard_report.json",
    "p0c3_status.md",
    "p0c3_status.json",
    "p0c3_final_review.md",
    "p0c3_final_review.json",
    "same_split_baseline_eval.md",
    "same_split_baseline_eval.json",
    "same_split_baseline_predictions.jsonl",
    "up_recall_slice_analysis.md",
    "up_recall_slice_analysis.json",
    "qwen_guard_ensemble_eval.md",
    "qwen_guard_ensemble_eval.json",
    "qwen_guard_ensemble_predictions.jsonl",
    "qwen_prompt_evidence_diagnosis.md",
    "qwen_prompt_evidence_diagnosis.json",
    "p0c4a_status.md",
    "p0c4a_status.json",
    "p0c4a_final_review.md",
    "p0c4a_final_review.json",
    "qwen_candidate_v2_dataset_report.md",
    "qwen_candidate_v2_dataset_report.json",
    "qwen_candidate_v2_prompt_plan.md",
    "qwen_candidate_v2_prompt_plan.json",
    "qwen_candidate_v2_training_plan.md",
    "qwen_candidate_v2_training_plan.json",
    "qwen_candidate_v2_eval_plan.md",
    "qwen_candidate_v2_eval_plan.json",
    "p0c4b_prep_status.md",
    "p0c4b_prep_status.json",
    "qwen_lora_v2_dataset_validation_report.md",
    "qwen_lora_v2_dataset_validation_report.json",
    "qwen_lora_v2_gpu_preflight.md",
    "qwen_lora_v2_gpu_preflight.json",
    "qwen_lora_v2_dryrun_report.md",
    "qwen_lora_v2_dryrun_report.json",
    "qwen_lora_v2_train_report.md",
    "qwen_lora_v2_train_report.json",
    "qwen_lora_v2_eval_report.md",
    "qwen_lora_v2_eval_metrics.json",
    "qwen_lora_v2_compare.md",
    "qwen_lora_v2_compare.json",
    "p0c4b_train_status.md",
    "p0c4b_train_status.json",
    "final_model_review.md",
    "final_model_review.json",
    "model_selection_report.md",
    "model_selection_report.json",
    "up_recall_failure_report.md",
    "up_recall_failure_report.json",
    "data_gap_report.md",
    "data_gap_report.json",
    "next_iteration_plan.md",
    "next_iteration_plan.json",
    "final_project_status.md",
    "external_data_inventory.md",
    "external_data_inventory.json",
    "external_data_quality_report.md",
    "external_data_quality_report.json",
    "external_feature_build_report.md",
    "external_feature_build_report.json",
    "baseline_after_data_expansion.md",
    "baseline_after_data_expansion.json",
    "backtest_after_data_expansion.md",
    "backtest_after_data_expansion.json",
    "guard_after_data_expansion.md",
    "guard_after_data_expansion.json",
    "guard_after_data_expansion_predictions.jsonl",
    "p0d_data_expansion_status.md",
    "p0d_data_expansion_status.json",
    "external_dataset_download_report.md",
    "external_dataset_download_report.json",
    "external_dataset_inventory_report.md",
    "external_dataset_inventory_report.json",
    "external_hdx_schema_probe_report.md",
    "external_hdx_schema_probe_report.json",
    "external_hdx_case_staging_report.md",
    "external_hdx_case_staging_report.json",
    "external_data_final_inventory.md",
    "external_data_final_inventory.json",
    "external_hdx_column_mapping_report.md",
    "external_hdx_column_mapping_report.json",
    "external_admin_matching_report.md",
    "external_admin_matching_report.json",
    "external_case_normalization_report.md",
    "external_case_normalization_report.json",
    "external_case_deduplication_report.md",
    "external_case_deduplication_report.json",
    "external_weather_alignment_report.md",
    "external_weather_alignment_report.json",
    "external_panel_build_report.md",
    "external_panel_build_report.json",
    "external_label_quality_report.md",
    "external_label_quality_report.json",
    "external_quality_gate_report.md",
    "external_quality_gate_report.json",
    "external_validation_plan.md",
    "external_validation_plan.json",
    "external_validation_dataset_report.md",
    "external_validation_dataset_report.json",
    "external_baseline_eval.md",
    "external_baseline_eval.json",
    "external_guard_eval.md",
    "external_guard_eval.json",
    "external_qwen_v1_eval.md",
    "external_qwen_v1_eval.json",
    "external_qwen_v2_eval.md",
    "external_qwen_v2_eval.json",
    "external_model_comparison.md",
    "external_model_comparison.json",
    "external_error_analysis.md",
    "external_error_analysis.json",
    "p0e_external_validation_status.md",
    "p0e_external_validation_status.json",
    "p0e_final_review.md",
    "p0e_final_review.json",
    "baseline_artifact_recovery_report.md",
    "baseline_artifact_recovery_report.json",
    "external_baseline_recovery_eval.md",
    "external_baseline_recovery_eval.json",
    "external_model_selection_review.md",
    "external_model_selection_review.json",
    "school_data_request_draft.md",
    "school_data_request_draft.json",
    "school_data_request_draft.docx",
    "v3_training_prerequisites.md",
    "v3_training_prerequisites.json",
    "p0f_status.md",
    "p0f_status.json",
    "v3_retraining_readiness_report.md",
    "v3_retraining_readiness_report.json",
    "p1b_source_registry_report.md",
    "p1b_source_registry_report.json",
    "p1b_download_inventory.md",
    "p1b_download_inventory.json",
    "p1b_prisma_dataset_links.md",
    "p1b_prisma_dataset_links.json",
    "p1b_prisma_query_report.md",
    "p1b_prisma_query_report.json",
    "p1b_source_download_report.md",
    "p1b_source_download_report.json",
    "p1b_admin_population_weather_download_report.md",
    "p1b_admin_population_weather_download_report.json",
    "p1b_mobility_download_report.md",
    "p1b_mobility_download_report.json",
    "external_v3_schema_probe_report.md",
    "external_v3_schema_probe_report.json",
    "external_v3_column_mapping_report.md",
    "external_v3_column_mapping_report.json",
    "external_v3_admin_matching_report.md",
    "external_v3_admin_matching_report.json",
    "external_v3_case_normalization_report.md",
    "external_v3_case_normalization_report.json",
    "external_v3_deduplication_report.md",
    "external_v3_deduplication_report.json",
    "external_v3_weather_alignment_report.md",
    "external_v3_weather_alignment_report.json",
    "external_v3_mobility_report.md",
    "external_v3_mobility_report.json",
    "external_v3_panel_build_report.md",
    "external_v3_panel_build_report.json",
    "external_v3_label_quality_report.md",
    "external_v3_label_quality_report.json",
    "external_v3_role_assignment_report.md",
    "external_v3_role_assignment_report.json",
}

FIGURE_FIND = r"""
if [ -d data/outputs/figures ]; then
  find data/outputs/figures -maxdepth 1 -type f \( \
    -name 'confusion_matrix_*.png' -o \
    -name 'feature_importance_*.png' -o \
    -name 'calibration_*.png' -o \
    -name 'qwen_lora_confusion_matrix.png' -o \
    -name 'qwen_error_breakdown.png' -o \
    -name 'up_misclassification_slices.png' -o \
    -name 'qwen_guard_ensemble_comparison.png' -o \
    -name 'qwen_lora_v2_confusion_matrix.png' \
    -o -name 'external_qwen_v1_confusion_matrix.png' \
    -o -name 'external_qwen_v2_confusion_matrix.png' \
    -o -name 'external_model_comparison.png' \
    -o -name 'external_error_breakdown.png' \
    -o -name 'external_baseline_recovery_confusion_matrix.png' \
    -o -name 'outbreak_risk_confusion_matrix.png' \
    -o -name 'outbreak_risk_precision_recall_curve.png' \
    -o -name 'outbreak_risk_feature_importance.png' \
  \) -printf '%f\n' | sort
fi
"""
P2B_FIGURE_FIND = r"""
if [ -d data/outputs/figures ]; then
  find data/outputs/figures -maxdepth 1 -type f \( \
    -name 'outbreak_external_error_breakdown.png' \
  \) -printf '%f\n' | sort
fi
"""

PROCESSED_FIND = r"""
if [ -d data/processed ]; then
  find data/processed -maxdepth 1 -type f \( \
    -name 'model_registry.json' -o \
    -name 'step_experience_buffer.jsonl' -o \
    -name 'model_registry_backup_*.json' \
  \) -printf '%f\n' | sort
fi
"""


def pull_figure(runner: SSHRunner, filename: str) -> int:
    remote = f"{runner.config.remote_dir}/data/outputs/figures/{filename}"
    local = ensure_parent(Path("data/outputs/figures") / filename)
    return runner.sftp_get_file(remote, local).returncode


def pull_processed_file(runner: SSHRunner, relative_path: str) -> int:
    remote = f"{runner.config.remote_dir}/{relative_path}"
    local = ensure_parent(Path(relative_path))
    return runner.sftp_get_file(remote, local).returncode


def pull_template_file(runner: SSHRunner, filename: str) -> int:
    remote = f"{runner.config.remote_dir}/data/outputs/templates/{filename}"
    local = ensure_parent(Path("data/outputs/templates") / filename)
    return runner.sftp_get_file(remote, local).returncode


def pull_manual_completion_package_file(runner: SSHRunner, filename: str) -> int:
    remote = f"{runner.config.remote_dir}/data/outputs/manual_completion_package/{filename}"
    local = ensure_parent(Path("data/outputs/manual_completion_package") / filename)
    return runner.sftp_get_file(remote, local).returncode


def package_text_reports(report_names: list[str]) -> Path:
    zip_path = LOCAL_REPORTS / TEXT_REPORT_ZIP
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(zip_path, "w", compression=ZIP_DEFLATED) as archive:
        for report_name in report_names:
            local_path = LOCAL_REPORTS / report_name
            if local_path.exists() and local_path.is_file():
                archive.write(local_path, arcname=report_name)
    return zip_path


def remote_has_p2b_status(runner: SSHRunner, cfg: ServerConfig) -> bool:
    result = runner.run(
        remote_project_prelude(cfg, create=False) + "test -f data/outputs/reports/p2b_status.json\n",
        "logs/remote/pull_reports_stage_probe_sanitized.log",
    )
    return result.returncode == 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Pull whitelisted P0a reports and figures only.")
    parser.add_argument("--root", default=".")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    cfg = ServerConfig.from_project_root(args.root)
    runner = SSHRunner(cfg, timeout=180, dry_run=args.dry_run)
    p2b_only = remote_has_p2b_status(runner, cfg)
    reports_to_pull = P2B_REPORTS if p2b_only else REPORTS
    pulled_reports: list[str] = []
    missing_reports: list[str] = []
    for report in reports_to_pull:
        result = pull_report(runner, report)
        if result == 0:
            pulled_reports.append(report)
        else:
            missing_reports.append(report)
    figure_script = P2B_FIGURE_FIND if p2b_only else FIGURE_FIND
    figure_listing = runner.run(remote_project_prelude(cfg, create=False) + figure_script, "logs/remote/pull_reports_sanitized.log")
    pulled_figures: list[str] = []
    missing_figures: list[str] = []
    if figure_listing.returncode == 0:
        for filename in [line.strip() for line in figure_listing.stdout.splitlines() if line.strip().endswith(".png")]:
            if pull_figure(runner, filename) == 0:
                pulled_figures.append(filename)
            else:
                missing_figures.append(filename)
    text_report_zip = package_text_reports(pulled_reports)
    pulled_processed: list[str] = []
    missing_processed: list[str] = []
    if not p2b_only:
        processed_listing = runner.run(remote_project_prelude(cfg, create=False) + PROCESSED_FIND, "logs/remote/pull_processed_sanitized.log")
        if processed_listing.returncode == 0:
            for filename in [line.strip() for line in processed_listing.stdout.splitlines() if line.strip()]:
                relative_path = f"data/processed/{filename}"
                if pull_processed_file(runner, relative_path) == 0:
                    pulled_processed.append(relative_path)
                else:
                    missing_processed.append(relative_path)
        else:
            for relative_path in ["data/processed/model_registry.json", "data/processed/step_experience_buffer.jsonl"]:
                if pull_processed_file(runner, relative_path) == 0:
                    pulled_processed.append(relative_path)
                else:
                    missing_processed.append(relative_path)
    pulled_templates: list[str] = []
    missing_templates: list[str] = []
    if not p2b_only:
        for filename in TEMPLATE_FILES:
            if pull_template_file(runner, filename) == 0:
                pulled_templates.append(f"data/outputs/templates/{filename}")
            else:
                missing_templates.append(f"data/outputs/templates/{filename}")
    pulled_manual_package: list[str] = []
    missing_manual_package: list[str] = []
    if not p2b_only:
        for filename in MANUAL_COMPLETION_PACKAGE_FILES:
            if pull_manual_completion_package_file(runner, filename) == 0:
                pulled_manual_package.append(f"data/outputs/manual_completion_package/{filename}")
            else:
                missing_manual_package.append(f"data/outputs/manual_completion_package/{filename}")
    payload = {
        "pulled_reports": pulled_reports,
        "missing_reports": missing_reports,
        "pulled_processed": pulled_processed,
        "missing_processed": missing_processed,
        "pulled_templates": pulled_templates,
        "missing_templates": missing_templates,
        "pulled_manual_completion_package": pulled_manual_package,
        "missing_manual_completion_package": missing_manual_package,
        "pulled_figures": pulled_figures,
        "missing_figures": missing_figures,
        "text_report_zip": text_report_zip.as_posix(),
        "forbidden_paths_not_pulled": ["models/", "lora_weights/", "logs/", "server files", "secrets"],
    }
    write_local_report("remote_pull_reports_report.md", "Remote Pull Reports Report", "```json\n" + json.dumps(payload, ensure_ascii=False, indent=2) + "\n```")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    blocking_missing = [name for name in missing_reports if name not in OPTIONAL_REPORTS]
    return 0 if not blocking_missing and not missing_templates and not missing_manual_package and figure_listing.returncode == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
