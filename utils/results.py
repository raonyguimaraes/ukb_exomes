from ukbb_qc.resources.variant_qc import var_annotations_ht_path
from ukbb_qc.resources.basics import release_ht_path
from ukb_common.utils.annotations import annotation_case_builder
from gnomad.utils.vep import process_consequences
from ukb_exomes.resources import *


def compute_lambda_gc_ht(result_type: str = 'gene', tranche: str = CURRENT_TRANCHE, cutoff: float = 0.0001):
    af = get_af_info_ht(result_type, tranche)
    mt = hl.read_matrix_table(get_results_mt_path(result_type, tranche))

    if result_type == 'gene':  # Gene Info
        af = af.filter(af.caf > cutoff)
        mt = mt.filter_rows(hl.is_defined(af.index(mt['annotation'], mt['gene_id'])))
        lambda_gc = hl.agg.filter(hl.is_defined(mt.Pvalue), hl.methods.statgen._lambda_gc_agg(mt.Pvalue))
        mt = mt.select_cols('n_cases', lambda_gc_skato=lambda_gc)
    else:  # Variant Info
        af = af.filter(af.AF > cutoff)
        mt = mt.filter_rows(hl.is_defined(af.index(mt['locus'], mt['alleles'])))
        lambda_gc = hl.agg.filter(hl.is_defined(mt.Pvalue), hl.methods.statgen._lambda_gc_agg(mt.Pvalue))
        mt = mt.select_cols('n_cases', lambda_gc=lambda_gc)

    ht = mt.cols()
    return ht


def compute_ukb_pheno_moments_ht(pheno_sex='both_sexes', phenocode: list = None):
    pheno = get_ukb_pheno_mt()
    if phenocode is not None:
        pheno = pheno.filter_cols(hl.literal(phenocode).contains(pheno.phenocode))
    pheno = pheno.annotate_cols(mean=hl.agg.mean(pheno[pheno_sex]),
                                std=hl.agg.stats(pheno[pheno_sex]).stdev)
    pheno = pheno.annotate_cols(variance=pheno.std ** 2,
                                skewness=hl.agg.mean((pheno[pheno_sex] - pheno.mean) ** 3) / (pheno.std ** 3),
                                kurtosis=hl.agg.mean((pheno[pheno_sex] - pheno.mean) ** 4) / (pheno.std ** 4))
    pheno = pheno.select_cols('mean', 'variance', 'skewness', 'kurtosis').cols()
    return pheno


def get_af_info_ht(result_type: str = 'gene', tranche: str = CURRENT_TRANCHE):
    vep = hl.read_table(var_annotations_ht_path('vep', *TRANCHE_DATA[tranche]))
    vep = process_consequences(vep)
    vep = vep.explode(vep.vep.worst_csq_by_gene_canonical)
    annotation = annotation_case_builder(vep.vep.worst_csq_by_gene_canonical)
    vep = vep.annotate(annotation=hl.if_else(hl.literal({'missense', 'LC'}).contains(annotation), 'missense|LC', annotation))
    vep = vep.filter((hl.is_defined(vep.annotation)) & (vep.annotation != 'non-coding'))
    vep = vep.select(vep.vep.worst_csq_by_gene_canonical.gene_id, vep.annotation)

    freq = hl.read_table(release_ht_path(*TRANCHE_DATA[tranche]))
    var_af = vep.annotate(AF=freq[vep.key].freq[0].AF)

    if result_type == 'gene':
        sum_af = var_af.group_by(var_af.annotation, var_af.gene_id).aggregate(caf=hl.agg.sum(var_af.AF))
        return sum_af
    else:
        return var_af


def get_sig_cnt_mt(phenocode: list = None, gene_symbol: list = None, level: float = 1e-6, tranche: str = CURRENT_TRANCHE):
    # Load and Combine Data
    af = get_af_info_ht(result_type='gene', tranche=tranche)
    mt = hl.read_matrix_table(get_results_mt_path(tranche=tranche))
    mt = mt.annotate_rows(caf=af[mt.gene_id, mt.annotation]['caf'])
    # Count Significant Hits per Gene for each Test
    mt = mt.annotate_rows(sig_pheno_cnt_skato=hl.agg.sum(mt.Pvalue < level),
                          sig_pheno_cnt_skat=hl.agg.sum(mt.Pvalue_SKAT < level),
                          sig_pheno_cnt_burden=hl.agg.sum(mt.Pvalue_Burden < level))
    # Count Significant Hits per Phenotype for each Test
    mt = mt.annotate_cols(sig_gene_cnt_skato=hl.agg.sum(mt.Pvalue < level),
                          sig_gene_cnt_skat=hl.agg.sum(mt.Pvalue_SKAT < level),
                          sig_gene_cnt_burden=hl.agg.sum(mt.Pvalue_Burden < level))
    if phenocode is not None:
        mt = mt.filter_cols(hl.literal(phenocode).contains(mt.phenocode))
    if gene_symbol is not None:
        mt = mt.filter_rows(hl.literal(gene_symbol).contains(mt.gene_symbol))
    return mt

def compare_gene_var_sig_cnt_ht(test_type: str = 'skato', level: float = 1e-6, tranche: str = CURRENT_TRANCHE):
    var = hl.read_matrix_table(get_results_mt_path('variant', tranche=tranche))
    gene = hl.read_matrix_table(get_results_mt_path(tranche=tranche))

    var = var.annotate_entries(var_sig=var.Pvalue < level)
    var = var.annotate_rows(annotation=hl.if_else(hl.literal({'missense', 'LC'}).contains(var.annotation), 'missense|LC', var.annotation), )
    var = var.group_rows_by('gene', 'annotation').aggregate(var_cnt=hl.agg.sum(var.var_sig))
    var = var.annotate_rows(gene_symbol=var.gene)

    test_type = test_type.lower()
    pvalue = 'Pvalue'
    pvalue = 'Pvalue_SKAT' if test_type == 'skat' else pvalue
    pvalue = 'Pvalue_Burden' if test_type == 'burden' else pvalue
    gene = gene.annotate_entries(gene_sig=hl.if_else((hl.is_defined(gene[pvalue])) & (gene[pvalue] < level), 1, 0))

    var = var.key_rows_by('gene_symbol', 'annotation')
    gene = gene.key_rows_by('gene_symbol', 'annotation')

    gene = gene.annotate_entries(var_cnt=var[gene.row_key, gene.col_key]['var_cnt'])
    gene = gene.annotate_entries(var_sig=hl.if_else((hl.is_defined(gene.var_cnt)) & (gene.var_cnt > 0), 2, 0))
    gene = gene.annotate_entries(var_gene_sig=gene.var_sig + gene.gene_sig)
    gene = gene.annotate_cols(gene_var_sig_cnt=hl.agg.count_where(gene.var_gene_sig == 3),
                              var_sig_cnt=hl.agg.count_where(gene.var_gene_sig == 2),
                              gene_sig_cnt=hl.agg.count_where(gene.var_gene_sig == 1),
                              none_sig_cnt=hl.agg.count_where(gene.var_gene_sig == 0))
    return gene.cols()
