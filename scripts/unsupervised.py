#! /usr/bin/env python
# -*- coding: utf-8 -*-
"""
A script that does unsupervised
classification on single cell data (Mainly used for Spatial Transcriptomics)
It takes a list of data frames as input and outputs :

 - the normalized/filtered counts as matrix (one for each dataset)
 - a scatter plot with the predicted classes for each spot 
 - a file with the predicted classes for each spot and the spot coordinates (one for each dataset)

The spots in the output file will have the index of the dataset
appended. For instance if two datasets are given the indexes will
be (1 and 2). 

The user can select what clustering algorithm to use
and what dimensionality reduction technique to use. 

Noisy spots (very few genes expressed) are removed using a parameter.
Noisy genes (expressed in very few spots) are removed using a parameter.

The user can optionally give a list of images
and image alignments to plot the predicted classes
on top of the image. Then one image for each dataset
will be generated.

@Author Jose Fernandez Navarro <jose.fernandez.navarro@scilifelab.se>
"""
import argparse
import sys
import os
import numpy as np
import pandas as pd
from sklearn.manifold import TSNE
from sklearn.decomposition import PCA, FastICA, SparsePCA
#from sklearn.cluster import DBSCAN
from sklearn.cluster import KMeans
from sklearn.cluster import AgglomerativeClustering
#from sklearn.preprocessing import scale
from stanalysis.visualization import scatter_plot, scatter_plot3d, histogram
from stanalysis.normalization import *
from stanalysis.alignment import parseAlignmentMatrix
import matplotlib.pyplot as plt

MIN_EXPRESION = 2

def linear_conv(old, min, max, new_min, new_max):
    return ((old - min) / (max - min)) * ((new_max - new_min) + new_min)
        
def main(counts_table_files, 
         normalization, 
         num_clusters, 
         clustering_algorithm, 
         dimensionality_algorithm,
         use_log_scale,
         apply_sample_normalization,
         num_exp_genes, 
         num_genes_keep,
         outdir,
         alignment_files, 
         image_files,
         num_dimensions):

    if len(counts_table_files) == 0 or any([not os.path.isfile(f) for f in counts_table_files]):
        sys.stderr.write("Error, input file/s not present or invalid format\n")
        sys.exit(1)
    
    if image_files is not None and len(image_files) > 0 and len(image_files) != len(counts_table_files):
        sys.stderr.write("Error, the number of images given as input is not the same as the number of datasets\n")
        sys.exit(1)           
   
    if alignment_files is not None and len(alignment_files) > 0 and len(alignment_files) != len(image_files):
        sys.stderr.write("Error, the number of alignments given as input is not the same as the number of images\n")
        sys.exit(1)   
                 
    if outdir is None or not os.path.isdir(outdir): 
        outdir = os.getcwd()
    outdir = os.path.abspath(outdir)
       
    num_exp_genes = num_exp_genes / 100.0
    num_genes_keep = num_genes_keep / 100.0
    
    # Spots are rows and genes are columns
    counts = pd.DataFrame()
    sample_counts = dict()
    for i,counts_file in enumerate(counts_table_files):
        new_counts = pd.read_table(counts_file, sep="\t", header=0, index_col=0)
        print "Processing dataset {} ...".format(counts_file)
        num_genes = len(new_counts.columns)
        num_spots = len(new_counts.index)
        total_reads = new_counts.sum().sum()
        print "Contains {} genes, {} spots and {} total counts".format(num_genes, num_spots, total_reads)
        histogram(x_points=new_counts.sum(axis=1).values,
                  output=os.path.join(outdir, "hist_reads_{}.png".format(i)))
        histogram(x_points=(new_counts != 0).sum(axis=1).values, 
                  output=os.path.join(outdir, "hist_genes_{}.png".format(i)))
    
        # Append dataset index to the spots (indexes)
        new_spots = ["{0}_{1}".format(i, spot) for spot in new_counts.index]
        new_counts.index = new_spots
        counts = counts.append(new_counts)
        if apply_sample_normalization:
            # Total sum of each gene for each sample
            sample_counts[i] = new_counts.sum(axis=0)
    # Replace Nan and Inf by zeroes
    counts.replace([np.inf, -np.inf], np.nan)
    counts.fillna(0.0, inplace=True)
    
    if len(counts_table_files) > 1:
        # Write aggregated matrix to file
        counts.to_csv(os.path.join(outdir, "aggregated_counts.tsv"), sep="\t")
               
    # Per sample normalization
    if apply_sample_normalization and len(counts_table_files) > 1:
        print "Computing per sample normalization..."
        # First build up a data frame with the accumulated gene counts for
        # each sample
        per_sample_factors = pd.DataFrame(index=sample_counts.keys(), columns=counts.columns)
        for key,value in sample_counts.iteritems():
            per_sample_factors.loc[key] = value
        # Replace Nan and Inf by zeroes
        per_sample_factors.replace([np.inf, -np.inf], np.nan)
        per_sample_factors.fillna(0.0, inplace=True)
        
        # Spots are columns and genes are rows
        per_sample_factors = per_sample_factors.transpose()
        
        # Compute normalization factors for each dataset(sample) using DESeq 
        per_sample_size_factors = computeSizeFactors(per_sample_factors)
        
        # Now use the factors per sample to normalize genes in each sample
        # one factor per sample so we divide every gene count of each sample by its factor
        for spot in counts.index:
            # spot is i_XxY
            tokens = spot.split("x")
            assert(len(tokens) == 2)
            index = int(tokens[0].split("_")[0])
            factor = per_sample_size_factors[index]
            counts.loc[spot] = counts.loc[spot] / factor
            
        # Replace Nan and Inf by zeroes
        counts.replace([np.inf, -np.inf], np.nan)
        counts.fillna(0.0, inplace=True)

    # How many spots do we keep based on the number of genes expressed?
    num_spots = len(counts.index)
    num_genes = len(counts.columns)
    min_genes_spot_exp = round((counts != 0).sum(axis=1).quantile(num_exp_genes))
    print "Number of expressed genes a spot must have to be kept " \
    "(1% of total expressed genes) {}".format(min_genes_spot_exp)
    counts = counts[(counts != 0).sum(axis=1) >= min_genes_spot_exp]
    print "Dropped {} spots".format(num_spots - len(counts.index))
          
    # Spots are columns and genes are rows
    counts = counts.transpose()
  
    # Remove noisy genes
    min_features_gene = round(len(counts.columns) * 0.01) 
    print "Removing genes that are expressed in less than {} " \
    "spots with a count of at least {}".format(min_features_gene, MIN_EXPRESION)
    counts = counts[(counts >= MIN_EXPRESION).sum(axis=1) >= min_features_gene]
    print "Dropped {} genes".format(num_genes - len(counts.index))
      
    print "Computing per spot normalization..." 
    # Per spot normalization
    if normalization in "DESeq":
        size_factors = computeSizeFactors(counts)
        norm_counts = counts / size_factors
    elif normalization in "DESeq2":
        size_factors = computeSizeFactorsLinear(counts)
        norm_counts = counts / size_factors
    elif normalization in "DESeq2Log":
        norm_counts = computeDESeq2LogTransform(counts)
    elif normalization in "EdgeR":
        size_factors = computeEdgeRNormalization(counts)
        # An alternative is to multiply by 10e6
        norm_counts = (counts / size_factors) * np.mean(size_factors)
    elif normalization in "REL":
        spots_sum = counts.sum(axis=0)
        norm_counts = counts / spots_sum
    elif normalization in "RAW":
        norm_counts = counts
    else:
        sys.stderr.write("Error, incorrect normalization method\n")
        sys.exit(1)
    
    # Keep only the genes with higher over-all variance
    # NOTE: this could be changed so to keep the genes with the highest expression
    min_genes_spot_var = norm_counts.var(axis=1).quantile(num_genes_keep)
    num_genes = len(norm_counts.index)
    print "Min normalized variance a gene must have over all spots " \
    "to be kept ({0}% of total) {1}".format(num_genes_keep, min_genes_spot_var)
    norm_counts = norm_counts[norm_counts.var(axis=1) >= min_genes_spot_var]
    print "Dropped {} genes".format(num_genes - len(norm_counts.index))
    
    # Spots as rows and genes as columns
    norm_counts = norm_counts.transpose()
    
    # Write normalized counts to different files
    tot_spots = norm_counts.index
    for i in xrange(len(counts_table_files)):
        spots_to_keep = [spot for spot in tot_spots if spot.startswith("{}_".format(i))]
        slice_counts = norm_counts.loc[spots_to_keep]
        slice_counts.index = [spot.split("_")[1] for spot in spots_to_keep]
        slice_counts.to_csv(os.path.join(outdir, "normalized_counts_{}.tsv".format(i)), sep="\t")
              
    if "tSNE" in dimensionality_algorithm:
        # method = barnes_hut or exact(slower)
        # init = pca or random
        # random_state = None or number
        # metric = euclidean or any other
        # n_components = 2 is default
        decomp_model = TSNE(n_components=num_dimensions, random_state=None, perplexity=5,
                            early_exaggeration=4.0, learning_rate=1000, n_iter=1000,
                            n_iter_without_progress=30, metric="euclidean", init="pca",
                            method="exact", angle=0.5, verbose=0)
    elif "PCA" in dimensionality_algorithm:
        # n_components = None, number of mle to estimate optimal
        decomp_model = PCA(n_components=num_dimensions, whiten=True, copy=True)
    elif "ICA" in dimensionality_algorithm:
        decomp_model = FastICA(n_components=num_dimensions, 
                               algorithm='parallel', whiten=True,
                               fun='logcosh', w_init=None, random_state=None)
    elif "SPCA" in dimensionality_algorithm:
        decomp_model = SparsePCA(n_components=num_dimensions, alpha=1)
    else:
        sys.stderr.write("Error, incorrect dimensionality reduction method\n")
        sys.exit(1)
    
    if use_log_scale:
        print "Using pseudo-log counts log2(counts + 1)"
        norm_counts = np.log2(norm_counts + 1)  
     
    print "Performing dimensionality reduction..."    
    # Perform dimensionality reduction, outputs a bunch of 2D coordinates
    reduced_data = decomp_model.fit_transform(norm_counts)
    
    # Do clustering of the dimensionality reduced coordinates
    if "KMeans" in clustering_algorithm:
        clustering = KMeans(init='k-means++', n_clusters=num_clusters, n_init=10)    
    elif "Hierarchical" in clustering_algorithm:
        clustering = AgglomerativeClustering(n_clusters=num_clusters, 
                                             affinity='euclidean',
                                             n_components=None, linkage='ward') 
    else:
        sys.stderr.write("Error, incorrect clustering method\n")
        sys.exit(1)

    print "Performing clustering..."  
    # Obtain predicted classes for each spot
    labels = clustering.fit_predict(reduced_data)
    if 0 in labels: labels = labels + 1
    
    # Compute a color_label based on the RGB representation of the 3D dimensionality reduced
    labels_colors = list()
    x_max = max(reduced_data[:,0])
    x_min = min(reduced_data[:,0])
    y_max = max(reduced_data[:,1])
    y_min = min(reduced_data[:,1])
    x_p = reduced_data[:,0]
    y_p = reduced_data[:,1]
    z_p = y_p
    if num_dimensions == 3:
        z_p = reduced_data[:,3]
        z_max = max(reduced_data[:,2])
        z_min = min(reduced_data[:,2])
    for x,y,z in zip(x_p,y_p,z_p):
        r = linear_conv(x, x_min, x_max, 0.0, 1.0)
        g = linear_conv(y, y_min, y_max, 0.0, 1.0)
        b = linear_conv(z, z_min, z_max, 0.0, 1.0) if num_dimensions == 3 else 1.0
        labels_colors.append((r,g,b))

    print "Generating plots..." 
     
    # Plot the clustered spots with the class color
    if num_dimensions == 3:
        scatter_plot3d(x_points=reduced_data[:,0], 
                       y_points=reduced_data[:,1],
                       z_points=reduced_data[:,2],
                       colors=labels, 
                       output=os.path.join(outdir,"computed_classes.png"), 
                       title='Computed classes', 
                       alpha=1.0, 
                       size=70)
    else:
        scatter_plot(x_points=reduced_data[:,0], 
                     y_points=reduced_data[:,1],
                     colors=labels, 
                     output=os.path.join(outdir,"computed_classes.png"), 
                     title='Computed classes', 
                     alpha=1.0, 
                     size=70)        
    
    # Write the spots and their classes to a file
    assert(len(labels) == len(norm_counts.index))
    # First get the spots coordinates
    x_points_index = [[] for ele in xrange(len(counts_table_files))]
    y_points_index = [[] for ele in xrange(len(counts_table_files))]
    labels_index = [[] for ele in xrange(len(counts_table_files))]
    labels_color_index = [[] for ele in xrange(len(counts_table_files))]
    file_writers = [open(os.path.join(outdir,"computed_classes_{}.txt".format(i)),"w") for i in xrange(len(counts_table_files))]
    # Write the coordinates and the label/class the belong to
    for i,bc in enumerate(norm_counts.index):
        # bc is i_XxY
        tokens = bc.split("x")
        assert(len(tokens) == 2)
        y = float(tokens[1])
        x = float(tokens[0].split("_")[1])
        index = int(tokens[0].split("_")[0])
        x_points_index[index].append(x)
        y_points_index[index].append(y)
        labels_index[index].append(labels[i])
        labels_color_index[index].append(labels_colors[i])
        file_writers[index].write("{0}\t{1}\n".format(labels[i], "{}x{}".format(x,y)))
        
    # Close the files
    for file_descriptor in file_writers:
        file_descriptor.close()
        
    # Create one image for each dataset
    for i,image in enumerate(image_files) if image_files else []:
        if image is not None and os.path.isfile(image):
            alignment_file = alignment_files[i] \
            if alignment_files is not None and len(alignment_files) >= i else None
            # alignment_matrix will be identity if alignment file is None
            alignment_matrix = parseAlignmentMatrix(alignment_file)            
            scatter_plot(x_points=x_points_index[i], 
                         y_points=y_points_index[i],
                         colors=labels_index[i], 
                         output=os.path.join(outdir,"computed_classes_tissue_{}.png".format(i)), 
                         alignment=alignment_matrix, 
                         cmap=None, 
                         title='Computed classes tissue', 
                         xlabel='X', 
                         ylabel='Y',
                         image=image, 
                         alpha=1.0, 
                         size=100)
            scatter_plot(x_points=x_points_index[i], 
                        y_points=y_points_index[i],
                        colors=labels_color_index[i], 
                        output=os.path.join(outdir,"dimensionality_color_tissue_{}.png".format(i)), 
                        alignment=alignment_matrix, 
                        cmap=plt.get_cmap("hsv"), 
                        title='Dimensionality color tissue', 
                        xlabel='X', 
                        ylabel='Y',
                        image=image, 
                        alpha=1.0, 
                        size=100)
             
                                
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--counts-table-files", required=True, nargs='+', type=str,
                        help="One or more matrices with gene counts per feature/spot (genes as columns)")
    parser.add_argument("--normalization", default="DESeq", metavar="[STR]", 
                        type=str, choices=["RAW", "DESeq", "DESeq2", "DESeq2Log", "EdgeR", "REL"],
                        help="Normalize the counts using RAW(absolute counts) , " \
                        "DESeq, DESeq2, DESeq2Log, EdgeR and " \
                        "REL(relative counts, each gene count divided by the total count of its spot) (default: %(default)s)")
    parser.add_argument("--num-clusters", default=3, metavar="[INT]", type=int, choices=range(2, 10),
                        help="The number of clusters/regions expected to be found. (default: %(default)s)")
    parser.add_argument("--num-exp-genes", default=10, metavar="[INT]", type=int, choices=range(0, 100),
                        help="The percentage of number of expressed genes ( != 0 ) a spot " \
                        "must have to be kept from the distribution of all expressed genes (default: %(default)s)")
    parser.add_argument("--num-genes-keep", default=20, metavar="[INT]", type=int, choices=range(0, 100),
                        help="The percentage of top variance genes to discard from the variance distribution of all the genes " \
                        "across all the spots (default: %(default)s)")
    parser.add_argument("--clustering", default="KMeans", metavar="[STR]", 
                        type=str, choices=["Hierarchical", "KMeans"],
                        help="What clustering algorithm to use after the dimensionality reduction " \
                        "(Hierarchical - KMeans) (default: %(default)s)")
    parser.add_argument("--dimensionality", default="ICA", metavar="[STR]", 
                        type=str, choices=["tSNE", "PCA", "ICA", "SPCA"],
                        help="What dimensionality reduction algorithm to use " \
                        "(tSNE - PCA - ICA - SPCA) (default: %(default)s)")
    parser.add_argument("--use-log-scale", action="store_true", default=False,
                        help="Use log values in the dimensionality reduction step")
    parser.add_argument("--normalize-samples", action="store_true", default=False,
                        help="When multiple datasets given this option computes normalization " \
                        "factors for each gene using DESeq on the different samples")
    parser.add_argument("--alignment-files", default=None, nargs='+', type=str,
                        help="One or more tab delimited files containing and alignment matrix for the images " \
                        "(array coordinates to pixel coordinates) as a 3x3 matrix in one row.\n" \
                        "Only useful is the image has extra borders, for instance not cropped to the array corners" \
                        "or if you want the keep the original image size in the plots.")
    parser.add_argument("--image-files", default=None, nargs='+', type=str,
                        help="When given the data will plotted on top of the image, " \
                        "It can be one ore more, ideally one for each input dataset\n " \
                        "It desirable that the image is cropped to the array corners otherwise an alignment file is needed")
    parser.add_argument("--num-dimensions", default=3, metavar="[INT]", type=int, choices=[2,3],
                        help="The number of dimensions to use in the dimensionality reduction (2 or 3). (default: %(default)s)")
    parser.add_argument("--outdir", default=None, help="Path to output dir")
    args = parser.parse_args()
    main(args.counts_table_files, args.normalization, int(args.num_clusters), 
         args.clustering, args.dimensionality, args.use_log_scale,
         args.normalize_samples, args.num_exp_genes, args.num_genes_keep, args.outdir, 
         args.alignment_files, args.image_files, args.num_dimensions)

