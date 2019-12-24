import tensorflow as tf
from pc_distance import tf_nndistance, tf_approxmatch


# ----------------------------------------------------------------------------------------------------------------------
#
#           Utilities
#       \***************/
#

def chamfer(pcd1, pcd2):
    # return 2
    dist1, _, dist2, _ = tf_nndistance.nn_distance(pcd1, pcd2)
    dist1 = tf.reduce_mean(tf.sqrt(dist1))
    dist2 = tf.reduce_mean(tf.sqrt(dist2))
    return (dist1 + dist2) / 2


def earth_mover(pcd1, pcd2):
    # return 2
    assert pcd1.shape[1] == pcd2.shape[1]
    num_points = tf.cast(pcd1.shape[1], tf.float32)
    match = tf_approxmatch.approx_match(pcd1, pcd2)
    cost = tf_approxmatch.match_cost(pcd1, pcd2, match)
    return tf.reduce_mean(cost / num_points)


def minimal_matching_distance(pcd_fine, dataset, compare_on_val=True):
    cd_gt_from_fine_list = []
    print(pcd_fine.shape)
    print("OIIIIIII")
    if compare_on_val:
        gt_list = dataset.complete_points['valid']
    else:
        gt_list = dataset.complete_points['train']

    for gt in gt_list:
        print(gt.shape)
        cd_gt_from_fine_list += [chamfer(pcd_fine, gt)]

    # print(cd_gt_from_fine_list)


