import json
import logging
from datetime import date, datetime

import requests

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


class G2PMTSConnector(models.Model):
    _inherit = "mts.connector"

    input_type = fields.Selection(
        [("odk", "ODK"), ("custom", "OpenG2P Registry")],
        string="MTS Input Type",
        required=True,
    )
    g2p_search_domain = fields.Text(
        string="Filters to apply to Registry",
        required=False,
        default="""[
            ["is_registrant","=", true],
            ["reg_ids.id_type","=like", "MOSIP VID"],
            "!", ["reg_ids.id_type","=like","MOSIP UIN TOKEN"]
        ]""",
    )
    g2p_selected_fields = fields.Text(
        string="List of fields to be used",
        required=False,
        default="""[
            "id",
            "given_name",
            "family_name",
            "birthdate",
            "gender",
            "address",
            "email",
            "phone"
        ]""",
    )

    @api.constrains("g2p_search_domain", "g2p_selected_fields")
    def constraint_g2p_mts_json_fields(self):
        for rec in self:
            if rec.g2p_search_domain:
                try:
                    json.loads(rec.g2p_search_domain)
                except ValueError as ve:
                    raise ValidationError(
                        _("'Filters to apply to Registry' is not valid json.")
                    ) from ve
            if rec.g2p_selected_fields:
                try:
                    json.loads(rec.g2p_selected_fields)
                except ValueError as ve:
                    raise ValidationError(
                        _("'List of fields to be used' is not valid json.")
                    ) from ve

    def custom_single_action(self):
        _logger.info("Custom Input action called.")

        config = self.env["ir.config_parameter"].sudo()
        vid_id_type = int(config.get_param("g2p_mts.vid_id_type"))
        # uin_token_id_type = int(config.get_param("g2p_mts.uin_token_id_type"))

        search_domain = json.loads(self.g2p_search_domain)
        selected_fields = json.loads(self.g2p_selected_fields)

        record_set = self.env["res.partner"].search(search_domain, limit=100)
        if len(record_set) > 0:
            record_list = record_set.read(selected_fields)
            for i, rec in enumerate(record_set):
                for reg_id in rec.reg_ids:
                    if reg_id.id_type.id == vid_id_type:
                        record_list[i]["vid"] = reg_id.value
                        break
            record_list = json.loads(
                json.dumps(record_list, default=self.record_set_json_serialize)
            )
            _logger.info("The recordset for debug %s", json.dumps(record_list))
            dt_utc = datetime.utcnow()
            mts_request = {
                "id": "string",
                "version": "string",
                "metadata": "string",
                "requesttime": dt_utc.strftime("%Y-%m-%dT%H:%M:%S")
                + dt_utc.strftime(".%f")[0:4]
                + "Z",
                "request": {
                    "output": self.output_type,
                    "deliverytype": self.delivery_type,
                    "mapping": json.loads(self.mapping),
                    "lang": self.lang_code,
                    "outputFormat": self.output_format,
                    "callbackProperties": {
                        "url": self.callback_url,
                        "httpMethod": self.callback_httpmethod,
                        "timeoutSeconds": self.callback_timeout,
                        "callInBulk": False,
                        "authType": self.callback_authtype,
                        "database": self.callback_auth_database,
                        "odooAuthUrl": self.callback_auth_url,
                        "username": self.callback_auth_username,
                        "password": self.callback_auth_password,
                    },
                    "authdata": record_list,
                },
            }
            mts_res = requests.post(
                "%s/authtoken/%s" % (self.mts_url, "json"), json=mts_request
            )
            _logger.info("Output of MTS %s", mts_res.text)
        if self.is_recurring == "onetime":
            self.job_status = "completed"

    def record_set_json_serialize(self, obj):
        if isinstance(obj, date):
            return obj.strftime("%Y/%m/%d")
        _logger.info("Cannot serialize obj type %s. Hence returning string", type(obj))
        return str(obj)

    def delete_vids_if_token(self):
        config = self.env["ir.config_parameter"].sudo()
        search_domain = json.loads(config.get_param("g2p_mts.vid_delete_search_domain"))
        vid_id_type = int(config.get_param("g2p_mts.vid_id_type"))
        uin_token_id_type = int(config.get_param("g2p_mts.uin_token_id_type"))

        for rec in self.env["res.partner"].search(search_domain):
            vid_reg_id = None
            uin_token_reg_id = None
            for reg_id in rec.reg_ids:
                if reg_id.id_type.id == vid_id_type:
                    vid_reg_id = reg_id
                if reg_id.id_type.id == uin_token_id_type:
                    uin_token_reg_id = reg_id
                if vid_reg_id and uin_token_reg_id:
                    break
            if uin_token_reg_id.status == "processed" and uin_token_reg_id.value:
                vid_reg_id.unlink()
