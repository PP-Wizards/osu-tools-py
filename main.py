from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor, Future, thread
import os
import threading
from time import time
import click
import requests
import slider
import logging

import calculators
from helpers.LightweightBeatmap import LightweightBeatmap
from helpers.Score import Score
from helpers.download_map import download_map
from helpers.table_print import print_scores

import json
from calculators.XexxarCalc.mlpp.weight_finder import weight_finder

RIPPLE_BASE_URL = "https://ripple.moe/api"
BANCHO_BASE_URL = "https://osu.ppy.sh/api"
MAX_THREADS = os.cpu_count() * 2

threadPool = ThreadPoolExecutor(max_workers=MAX_THREADS)
futures = []

LOG_LEVELS = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
}

def calculateMapFromScore(ctx, score: Score):
    logging.debug("Loading map...")
    now = time()
    beatmap_ = slider.Beatmap.from_path(f"./osu_files/{score.beatmap_id}.osu")
    logging.debug(f"Loaded map (Beatmap {score.beatmap_id}) in thread {threading.get_ident()} at {time() - now} seconds")
    calculator = calculators.PP_CALCULATORS[ctx.obj["calculator"]](beatmap_, score)
    logging.debug(f"Calculator (Beatmap {score.beatmap_id}) done in thread {threading.get_ident()} at {time() - now} seconds")
    logging.debug(f"Before: {score.pp}pp | After: {calculator.pp}pp")
    score.pp = calculator.pp
    logging.info(f"Calculated Score (Beatmap {score.beatmap_id}) in thread {threading.get_ident()} at {time() - now} seconds")
    return (score, LightweightBeatmap(beatmap_id=beatmap_.beatmap_id, display_name=beatmap_.display_name, max_combo=beatmap_.max_combo))

@click.group()
@click.pass_context
@click.option("--calculator", default="xexxar_v1", help="Calculator to use, default is xexxar_v1")
@click.option("--log", default="info", help="Log level, default is debug. Allowed is debug, info")
@click.option("--output", default="", help="Output file, if not specified, output is printed to stdout")
def cli(ctx, calculator, log, output):
    ctx.ensure_object(dict)
    ctx.obj['calculator'] = calculator
    ctx.obj['log'] = log
    ctx.obj['output'] = output
    logging.basicConfig(level=LOG_LEVELS[log], format="[%(asctime)s] [%(levelname)s] %(message)s")
    logging.debug(f"Using calculator: {calculator}")

@cli.command()
@click.pass_context
@click.argument("gamemode", type=str, nargs=1)
@click.argument("profile", type=str, nargs=1)
@click.option("--api-key", default="NONE", help="API key for bancho. https://osu.ppy.sh/p/api")
def bancho(ctx, profile, gamemode, api_key):
    click.echo("Bancho Profile Recalculator")
    click.echo(f"Passed profile = {profile}")

    if gamemode != "osu":
        click.echo("Only osu gamemode is supported")
        return
    

    params = {
        "u": profile,
        "k": api_key,
        "m": 0,
        "limit": 100
    }

    # get user
    resp = requests.get(BANCHO_BASE_URL + "/get_user", params=params)
    if  not resp.ok:
        click.echo("Failed to get user")
        return

    originalUser = resp.json()[0]


    resp = requests.get(BANCHO_BASE_URL + "/get_user_best", params=params)
    if not resp.ok:
        click.echo("Failed to get user best")
        return
    
    user_best = resp.json()
    if len(user_best) == 0:
        click.echo("No scores found")
        return

    scoresOriginal = []
    for score in user_best:
        temp = Score()
        temp.score = score["score"]
        temp.beatmap_id = score["beatmap_id"]
        temp.playerUserID = originalUser["user_id"]
        temp.playerName = originalUser["username"]
        temp.completed = True
        temp.c300 = score["count300"]
        temp.c100 = score["count100"]
        temp.c50 = score["count50"]
        temp.maxCombo = score["maxcombo"]
        temp.cMiss = score["countmiss"]
        temp.mods = score["enabled_mods"]
        temp.pp = score["pp"]

        scoresOriginal.append(temp)

    scoresRecalculated = []
    for score in scoresOriginal:
        # make sure the beatmap_id is reasonable (e.g. above 0)
        if int(score.beatmap_id) < 1:
            click.echo(f"Error: Beatmap ID is below 1 ({score.beatmap_id}), skipping")
            continue

        # Download the beatmap for caching purposes
        download_map(score.beatmap_id)
        print("Calculating score for beatmap " + str(score.beatmap_id))
        beatmap_ = slider.Beatmap.from_path(f"./osu_files/{score.beatmap_id}.osu")
        calculator = calculators.PP_CALCULATORS[ctx.obj["calculator"]](beatmap_, score)
        score.pp = calculator.pp
        scoresRecalculated.append(score)

    # Aggregate the pp of the recalculated scores, where
    # total pp = pp[1] * 0.95^0 + pp[2] * 0.95^1 + pp[3] * 0.95^2 + ... + pp[m] * 0.95^(m-1)
    copyProfile = deepcopy(originalUser)
    copyProfile["pp_raw"] = 0
    i = 0
    for score in scoresRecalculated:
        copyProfile["pp_raw"] += score.pp * (0.95 ** i)
        i += 1

    # print both the old and new profiles
    click.echo(f"Before: {json.dumps(originalUser['pp_raw'], indent=4)}")
    click.echo(f"After: {json.dumps(copyProfile['pp_raw'], indent=4)}")

    pass

@cli.command()
@click.pass_context
@click.argument("gamemode", type=str, nargs=1)
@click.argument("profile_id", type=int, nargs=1)
def ripple(ctx, gamemode, profile_id):
    logging.info("Ripple Profile Recalculator")

    logging.debug(f"Passed PROFILE_ID = {profile_id}")

    # only allow gamemode == "std" for now
    if gamemode != "std":
        logging.error("Only std gamemode is supported")
        return

    # get the current player's full profile from /v1/users/full
    params = {
        "id": profile_id
    }
    resp = requests.get(RIPPLE_BASE_URL + "/v1/users/full", params=params)
    if not resp.ok:
        logging.error("Error: " + str(resp.status_code))
        logging.error("Make sure the user isnt restricted.")
        return
    originalUser = resp.json()

    # Make a request to the ripple api for /get_user_best
    # This will return a list of scores for the user
    # We will then use the calculator to calculate the new score
    params = {
        "u": profile_id,
        "limit": 100,
        "relax": 0
    }
    resp = requests.get(RIPPLE_BASE_URL + "/get_user_best", params=params)
    if not resp.ok:
        logging.error("Error: " + str(resp.status_code))
        logging.error("Make sure the user isnt restricted.")
        return
    respJson = resp.json()
    scoresOriginal = {}
    for score in respJson:
        temp = Score()
        temp.score = int(score["score"])
        temp.beatmap_id = int(score["beatmap_id"])
        temp.playerUserID = int(profile_id)
        temp.playerName = originalUser["username"]
        temp.completed = True
        temp.c300 = int(score["count300"])
        temp.c100 = int(score["count100"])
        temp.c50 = int(score["count50"])
        temp.maxCombo = int(score["maxcombo"])
        temp.cMiss = int(score["countmiss"])
        temp.mods = int(score["enabled_mods"])
        temp.pp = score["pp"]

        scoresOriginal[temp.beatmap_id] = temp

    maps = {}

    # filter out any score where beatmap_id = 0
    scoresOriginal = {k: v for k, v in scoresOriginal.items() if v.beatmap_id != 0}

    scoresRecalculated = deepcopy(scoresOriginal)

    threaded_start = time()

    for map_id in scoresRecalculated:
        now = time()
        try:
            score = scoresRecalculated[map_id]
            # make sure the beatmap_id is reasonable (e.g. above 0)
            if int(map_id) < 1:
                logging.debug(f"Error: Beatmap ID is below 1 ({score.beatmap_id}), skipping")
                continue

            # Download the beatmap for caching purposes
            download_map(score.beatmap_id)
            logging.debug("Calculating score for beatmap " + str(score.beatmap_id))

            # Add the calculation part to the threadpool
            future = threadPool.submit(calculateMapFromScore, ctx, score)
            futures.append(future)
        except:
            None
        logging.debug(f"Added map to threadpool in thread {threading.get_ident()} in {time() - now} seconds")

    # Resolve the futures
    for future in futures:
        score, beatmap = future.result()
        scoresRecalculated[score.beatmap_id] = score
        maps[score.beatmap_id] = beatmap

    logging.debug("Threaded time:", time() - threaded_start)

    # sort the recalculated scores by their pp, highest first
    scoresRecalculatedArr = sorted(scoresRecalculated.values(), key=lambda x: float(x.pp), reverse=True)

    # Aggregate the pp of the recalculated scores, where
    # total pp = pp[1] * 0.95^0 + pp[2] * 0.95^1 + pp[3] * 0.95^2 + ... + pp[m] * 0.95^(m-1)
    copyProfile = deepcopy(originalUser)
    copyProfile[gamemode]["pp"] = 0
    i = 0
    for score in scoresRecalculatedArr:
        copyProfile[gamemode]["pp"] += score.pp * (0.95 ** i)
        i += 1

    print_scores(scoresOriginal, scoresRecalculatedArr, maps, ctx.obj['output'])

    # print both the old and new profiles
    logging.info(f"Profile Before: {originalUser[gamemode]['pp']}pp")
    logging.info(f"Profile After: {copyProfile[gamemode]['pp']}pp")
    pass

@cli.command()
@click.pass_context
@click.argument("file", nargs=1)
def weightfinder(ctx, file):
    with open(file, "r") as f:
        mapdata = json.load(f)

    weights = weight_finder(mapdata)

    logging.debug(weights)
    pass

@cli.command()
@click.pass_context
@click.argument("beatmap_id", nargs=1)
def web(ctx, beatmap_id):
    logging.info("Web (beatmap_id) Recalculator")
    logging.info("Currently not implemented")

    logging.debug(f"Passed ID = {beatmap_id}")
    pass


@cli.command()
@click.pass_context
@click.argument("beatmap")
def file(ctx, beatmap):
    logging.info("Osu! File Recalculator")

    # generate a beatmap object from the file
    beatmap_ = slider.Beatmap.from_path(beatmap)

    # Make a score here
    score_ = Score()
    score_.c300 = 500
    score_.mods = 64 | 4 | 8
    # etc etc

    calculator = calculators.PP_CALCULATORS[ctx.obj["calculator"]](beatmap_=beatmap_, score_=score_)
    logging.debug(f"PP Should already be set, its: {calculator.pp}")

if __name__ == "__main__":
    try:
        cli(obj={})
    except Exception as e:
        import traceback
        traceback.print_exc()
