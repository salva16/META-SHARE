
import sys
import logging

from django.test import TestCase

from metashare.settings import ROOT_PATH, LOG_HANDLER
from metashare.storage.models import MASTER
from metashare.repository.models import personInfoType_model
from metashare import test_utils

# Setup logging support.
LOGGER = logging.getLogger(__name__)
LOGGER.addHandler(LOG_HANDLER)

TESTFIXTURE_XML = '{}/repository/test_fixtures/person1.xml'.format(ROOT_PATH)

class TestRel(TestCase):


    @classmethod
    def setUpClass(cls):
        LOGGER.info("running '{}' tests...".format(cls.__name__))
        test_utils.set_index_active(False)
    
    @classmethod
    def tearDownClass(cls):
        test_utils.set_index_active(True)
        LOGGER.info("finished '{}' tests".format(cls.__name__))
    
    def setUp(self):
        pass


    def tearDown(self):
        """
        Clean up the test
        """
        test_utils.clean_resources_db()
        test_utils.clean_storage()


    def testName(self):
        _xml = open(TESTFIXTURE_XML)
        _xml_string = _xml.read()
        _xml.close()
        result = personInfoType_model.import_from_string(_xml_string)
        person = result[0]
        LOGGER.info("person = {}".format(person))
        LOGGER.info("person id = {}".format(person.id))
        self.assertEquals(1, 1)

