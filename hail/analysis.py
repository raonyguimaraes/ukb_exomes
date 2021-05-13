import hail as hl
import argparse
from gnomad.utils.vep import process_consequences
from gnomad.resources.grch38.reference_data import clinvar
from gnomad.resources.resource_utils import DataException
from gnomad.utils.vep import vep_or_lookup_vep
from ukbb_common import *
from ukb_exomes import *


def main(args):
    filter_flag = '_filtered' if args.filters else ''
    if args.update_main_tables:
        gene_mt = hl.read_matrix_table('gs://ukbb-pharma-exome-analysis-300k/300k/results/results.mt')
        var_mt = hl.read_matrix_table('gs://ukbb-pharma-exome-analysis-300k/300k/results/variant_results.mt')

        gene_mt = gene_mt.filter_cols(~(hl.set({'biogen', 'abbvie', 'pfizer'}).contains(gene_mt.modifier) | gene_mt.phenocode.endswith('_pfe')) & (gene_mt.n_cases >= 100))
        var_mt = var_mt.filter_cols(~(hl.set({'biogen', 'abbvie', 'pfizer'}).contains(var_mt.modifier) | var_mt.phenocode.endswith('_pfe')) & (gene_mt.n_cases >= 100))

        gene_mt.write(get_results_mt_path('gene'), overwrite=args.overwrite)
        var_mt.write(get_results_mt_path('variant'), overwrite=args.overwrite)
        gene_mt.cols().write(get_results_mt_path('phenotype'), overwrite=args.overwrite)

    if args.update_util_tables:
        coverage = compute_mean_coverage_ht()
        coverage.write(get_util_info_path('coverage'), overwrite=args.overwrite)
        caf = get_caf_info_ht()
        caf.write(get_util_info_path('caf'), overwrite=args.overwrite)

    if args.update_lambda_tables:
        write_lambda_hts(result_type='gene', freq_lower=args.freq_lower, n_var_min=args.n_var_min, coverage_min=args.coverage_min, extension='ht', overwrite=args.overwrite)
        compute_lambda_gc_ht(result_type='gene').write(get_ukb_exomes_sumstat_path(subdir='qc/lambda_gc', dataset='lambda_by_pheno_full'), overwrite=args.overwrite)

    if args.update_pheno_corr_tables:
        phenos_to_remove = get_corr_phenos_ht(r_2=0.5, tie_breaker=more_cases_tie_breaker)
        phenos_to_remove.write(get_ukb_exomes_sumstat_path(subdir='qc', dataset='correlated', result_type='phenos'), overwrite=args.overwrite)

        pheno_mt = get_ukb_pheno_mt()
        ht = hl.read_matrix_table(get_results_mt_path()).cols()
        pheno_mt = pheno_mt.filter_cols(hl.is_defined(ht[pheno_mt.col_key]))
        corr = make_pairwise_ht(pheno_mt, pheno_field=pheno_mt.both_sexes, correlation=True)
        corr.entry.export(get_ukb_exomes_sumstat_path(subdir='qc', dataset='pheno_correlation_before_filter', result_type='', extension='txt.bgz'))

        if args.get_related_pheno_cnts:
            pheno_ht = hl.read_table(get_results_mt_path('phenotype'))
            print(get_related_pheno_cnt_list(phenos_ht))

    if args.update_qc_tables:
        gene = hl.read_matrix_table(get_results_mt_path())
        var = hl.read_matrix_table(get_results_mt_path('variant'))
        gene = annotate_additional_info_mt(gene)

        pheno_lambda = hl.read_table(get_ukb_exomes_sumstat_path(subdir='qc/lambda_gc', dataset='lambda_by_pheno_full_filtered'))
        pheno_lambda = pheno_lambda.select(*{f'lambda_gc_{test}' for test in TESTS})
        pheno_corr = hl.read_table(get_ukb_exomes_sumstat_path(subdir='qc', dataset='correlated', result_type='phenos'))

        gene_lambda = hl.read_table(get_ukb_exomes_sumstat_path(subdir='qc/lambda_gc', dataset='lambda_by_gene_filtered', result_type=''))
        gene_lambda = gene_lambda.rename({f'all_lambda_gc_{test}': f'annotation_lambda_gc_{test}' for test in TESTS})
        gene_lambda = gene_lambda.drop(*{f'lambda_gc_{test}' for test in TESTS})
        for test in TESTS: gene_lambda = annotate_gene_lambda_filter_ht(gene_lambda, test)

        gene = gene.annotate_cols(**pheno_lambda[gene.col_key])
        gene = gene.annotate_rows(**gene_lambda[gene.row_key])
        gene = gene.annotate_cols(**{f'keep_pheno_{test}':(gene[f'lambda_gc_{test}'] > args.lambda_lower) & (gene[f'lambda_gc_{test}'] < args.lambda_upper) for test in TESTS},
                                  keep_pheno_unrelated=hl.is_missing(pheno_corr.key_by(trait_type=pheno_corr.node.trait_type, phenocode=pheno_corr.node.phenocode,
                                                                                       pheno_sex=pheno_corr.node.pheno_sex, coding=pheno_corr.node.coding,
                                                                                       modifier=pheno_corr.node.modifier, )[gene.col_key]))
        gene = gene.annotate_rows(**{f'keep_gene_{test}':(gene[f'synonymous_lambda_gc_{test}'] > args.lambda_lower) & (gene[f'synonymous_lambda_gc_{test}'] < args.lambda_upper) for test in TESTS},
                                  keep_gene_coverage = gene.mean_coverage > args.coverage_min,
                                  keep_gene_caf = gene.CAF > args.freq_lower,
                                  keep_gene_n_var = gene.total_variants >= args.n_var_min)

        var = var.annotate_cols(**pheno_lambda[var.col_key])
        var = var.annotate_cols(**{f'keep_pheno_{test}':(var[f'lambda_gc_{test}'] > args.lambda_lower) & (var[f'lambda_gc_{test}'] < args.lambda_upper) for test in TESTS},
                                keep_pheno_unrelated=hl.is_missing(pheno_corr.key_by(trait_type=pheno_corr.node.trait_type, phenocode=pheno_corr.node.phenocode,
                                                                                     pheno_sex=pheno_corr.node.pheno_sex, coding=pheno_corr.node.coding,
                                                                                     modifier=pheno_corr.node.modifier)[var.col_key]))
        var = var.annotate_rows(annotation=hl.if_else(hl.literal({'missense', 'LC'}).contains(var.annotation), 'missense|LC', var.annotation),
                                keep_var_af=var.AF > args.freq_lower,
                                keep_var_annt=hl.is_defined(var.annotation))

        gene.write(get_ukb_exomes_sumstat_path(subdir='qc', dataset='gene_qc_metrics_ukb_exomes', result_type='', extension='mt'), overwrite=args.overwrite)
        gene.rows().write(get_ukb_exomes_sumstat_path(subdir='qc', dataset='gene_qc_metrics_ukb_exomes', result_type='', extension='ht'), overwrite=args.overwrite)
        gene.cols().write(get_ukb_exomes_sumstat_path(subdir='qc', dataset='pheno_qc_metrics_ukb_exomes', result_type='', extension='ht'), overwrite=args.overwrite)
        var.write(get_ukb_exomes_sumstat_path(subdir='qc', dataset='variant_qc_metrics_ukb_exomes', result_type='', extension='mt'), overwrite=args.overwrite)
        var.rows().write(get_ukb_exomes_sumstat_path(subdir='qc', dataset='variant_qc_metrics_ukb_exomes', result_type='', extension='ht'), overwrite=args.overwrite)

    if args.update_sig_cnt_tables:
        gene = get_sig_cnt_mt(result_type='gene', test_type=args.test_type, filters=args.filters)
        gene_sig = gene.rows()
        gene_sig.write(get_ukb_exomes_sumstat_path(subdir='analysis', dataset=f'gene_sig_cnt{filter_flag}_{args.test_type}', result_type=''), overwrite=args.overwrite)
        pheno_sig = gene.cols()
        pheno_sig.write(get_ukb_exomes_sumstat_path(subdir='analysis', dataset=f'pheno_sig_cnt{filter_flag}_{args.test_type}', result_type='gene'), overwrite=args.overwrite)

        var = get_sig_cnt_mt(result_type='variant', test_type=args.test_type, filters=args.filters)
        var = annotate_additional_info_mt(var, 'variant')
        var_sig = var.rows()
        var_sig.write(get_ukb_exomes_sumstat_path(subdir='analysis', dataset=f'var_sig_cnt{filter_flag}_{args.test_type}', result_type=''), overwrite=args.overwrite)
        pheno_var_sig = var.cols()
        pheno_var_sig.write(get_ukb_exomes_sumstat_path(subdir='analysis', dataset=f'pheno_sig_cnt{filter_flag}_{args.test_type}', result_type='var'), overwrite=args.overwrite)

        var_gene = compare_gene_var_sig_cnt_mt(test_type=args.test_type, filters=args.filters)
        var_gene_after.cols().write(
            get_ukb_exomes_sumstat_path(subdir='analysis', dataset=f'var_gene_comparison_by_pheno{filter_flag}_{args.test_type}',result_type=''), overwrite=args.overwrite)

    if args.update_icd_tables:
        icd_var = get_icd_min_p_ht(result_type='variant', test_type=args.test_type, filters=args.filters)
        icd_gene = get_icd_min_p_ht(result_type='gene', test_type=args.test_type, filters=args.filters)
        icd_var.write(get_ukb_exomes_sumstat_path(subdir='analysis', dataset=f'icd_min_p_var{filter_flag}_{args.test_type}', result_type=''))
        icd_gene.write(get_ukb_exomes_sumstat_path(subdir='analysis', dataset=f'icd_min_p_gene{filter_flag}_{args.test_type}', result_type=''))

    if args.convert_to_txt_bgz:
        export_all_ht_to_txt_bgz('qc/lambda_gc')
        export_all_ht_to_txt_bgz('qc')
        export_all_ht_to_txt_bgz('analysis')
        export_all_ht_to_txt_bgz('results')

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--overwrite', help='Overwrite everything', action='store_true')
    parser.add_argument('--update_main_tables', help='Update the original result tables', action='store_true')
    parser.add_argument('--update_util_tables', help='Update the utility tables', action='store_true')
    parser.add_argument('--update_lambda_tables', help='Update the lambda gc tables', action='store_true')
    parser.add_argument('--update_pheno_corr_tables', help='Update the phenotype correlation tables', action='store_true')
    parser.add_argument('--update_qc_tables', help='Update the tables with QC metrics', action='store_true')
    parser.add_argument('--update_sig_cnt_tables', help='Update significant association tables', action='store_true')
    parser.add_argument('--update_icd_tables', help='Update minimum pvalue tables for icd phenotypes', action='store_true')
    parser.add_argument('--convert_to_txt_bgz', help='Convert all hail Tables to .txt.bgz file', action='store_true')
    parser.add_argument('--get_related_pheno_cnts', help='Count the number of correlated phenotypes to remove', action='store_true')
    parser.add_argument('--coverage_min', help='Keep genes with higher coverage', action='store_true')
    parser.add_argument('--n_var_min', help='Keep genes with larger number of variants', action='store_true')
    parser.add_argument('--freq_lower', help='Keep genes/variants with higher cumulative allele frequency', action='store_true')
    parser.add_argument('--lambda_lower', help='Remove genes/phenotypes with lower lambda value', action='store_true')
    parser.add_argument('--lambda_upper', help='Remove genes/phenotypes with higher lambda value', action='store_true')
    parser.add_argument('--test_type', help='Test results to apply lambda filters on: skato OR burden', action='store_true')
    parser.add_argument('--filters', help='Apply filters', action='store_true')
    args = parser.parse_args()
    print(args)

    main(args)
