import logging

class CustomFormatter(logging.Formatter):
    def formatTime(self, record, datefmt=None):
        custom_time_format = "%Y-%m-%d %H:%M:%S"
        return super().formatTime(record, datefmt=custom_time_format)


def configure_global_log_levels():
    # Set specific log levels for external libraries
    logging.getLogger('googleapiclient.discovery_cache').setLevel(logging.ERROR)
    logging.getLogger('requests.packages.urllib3.connectionpool').setLevel(logging.CRITICAL)


def get_custom_logger(name):
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    
    # Prevent logger from propagating messages to the root logger
    logger.propagate = False

    # Check if the logger already has handlers to prevent duplicate messages
    if not logger.handlers:
        # Create console handler and set level to info
        ch = logging.StreamHandler()
        ch.setLevel(logging.INFO)

        # Create formatter and add it to the handlers
        formatter = CustomFormatter('%(asctime)s %(levelname)s - %(message)s')
        ch.setFormatter(formatter)

        # Add the handlers to the logger
        logger.addHandler(ch)
    
    return logger

# Configure root logger level
logging.basicConfig(level=logging.INFO)

# Apply global log level configurations
configure_global_log_levels()