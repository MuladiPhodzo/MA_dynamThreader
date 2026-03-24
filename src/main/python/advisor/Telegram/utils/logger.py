from advisor.utils.logging_setup import configure_logging, get_logger


def setup_logger(name="advisor", logfile="advisor.log"):
    configure_logging(log_file=logfile)
    return get_logger(name)
